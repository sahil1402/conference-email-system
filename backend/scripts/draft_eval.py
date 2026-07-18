"""End-to-end draft evaluation on real tickets (guide A/B).

Test-roadmap step 5 (docs/PIPELINE_AUDIT.md): for every policy-answerable
labeled ticket, generate a reply draft through the REAL pipeline path —
keyword classify -> retrieve top-MAX_RETRIEVED_CHUNKS over the real KB ->
rule-based route -> ResponseDrafter(provider="local") against the hosted
chat-completions endpoint — once per style-guide config:

  none  STYLE_GUIDE_PATH unset (base grounding prompt only)
  v1    data/style_guide/style_guide_v1.md (distilled, ~1.1k words)
  v2    data/style_guide/style_guide_v2.md (curated, ~460 words)

Drafts go to data/eval_real/drafts.jsonl (gitignored; resumable by
(ticket_id, config)). Judging happens separately (blinded packets for
independent judges — see build_judge_packets).

Usage:
    python scripts/draft_eval.py drafts [--model <id>] [--backend dense|bm25]
    python scripts/draft_eval.py packets [--per-batch 12]
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.config import settings  # noqa: E402
from app.pipeline import drafter as drafter_module  # noqa: E402
from app.pipeline.classifier import keyword_classify  # noqa: E402
from app.pipeline.drafter import ResponseDrafter  # noqa: E402
from app.pipeline.retriever import RetrievedChunk  # noqa: E402
from app.pipeline.router import EmailRouter  # noqa: E402
from distill_style_guide import read_key  # noqa: E402
from scripts._kb_retriever import build_retriever_from_kb  # noqa: E402

KB_PATH = REPO_ROOT / "data" / "knowledge_base" / "policies.json"
EVAL_DIR = REPO_ROOT / "data" / "eval_real"
SAMPLE_PATH = EVAL_DIR / "sample.jsonl"
LABELS_PATH = EVAL_DIR / "labels.jsonl"
DRAFTS_PATH = EVAL_DIR / "drafts.jsonl"
PACKET_DIR = EVAL_DIR / "judge_batches"

DEFAULT_MODEL = "gpt-5.5"
CONCURRENCY = 5

# v2 vs none only (user direction 2026-07-17 — v1 excluded from the A/B).
GUIDE_CONFIGS = {
    "none": None,
    "v2": str(REPO_ROOT / "data" / "style_guide" / "style_guide_v2.md"),
}


def load_answerable() -> list[dict]:
    samples = {r["ticket_id"]: r for r in map(json.loads, open(SAMPLE_PATH, encoding="utf-8"))}
    rows = []
    for line in open(LABELS_PATH, encoding="utf-8"):
        l = json.loads(line)
        if l["policy_answerable"] and l["relevant_chunk_ids"] and l["ticket_id"] in samples:
            rows.append({**samples[l["ticket_id"]], **{"gold": l["relevant_chunk_ids"]}})
    return rows


# ---------------------------------------------------------------------------
# Retrieval (dense mirrors faiss_retriever.py; bm25 is the real class)
# ---------------------------------------------------------------------------
class Retriever:
    def __init__(self, backend: str) -> None:
        self.backend = backend
        self.chunks = json.load(open(KB_PATH, encoding="utf-8"))
        self._bm25 = build_retriever_from_kb(KB_PATH)
        self._by_id = {c["id"]: c for c in self.chunks}
        if backend in ("dense", "fusion"):
            import torch

            torch.set_num_threads(4)
            from sentence_transformers import SentenceTransformer

            self._embedder = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
            self._doc_vecs = self._embedder.encode(
                [f"{c['title']} {c['content']}" for c in self.chunks],
                normalize_embeddings=True,
            )

    def _dense_ranking(self, text: str) -> list[str]:
        import numpy as np

        qv = self._embedder.encode([text], normalize_embeddings=True)
        order = np.argsort(-(self._doc_vecs @ qv.T).ravel())
        return [self.chunks[i]["id"] for i in order]

    async def retrieve(self, query: str, intent: str, top_k: int) -> list[RetrievedChunk]:
        if self.backend == "bm25":
            return await self._bm25.retrieve(query, intent=intent, top_k=top_k)
        text = f"{query} {intent}".strip()
        if self.backend == "dense":
            top = self._dense_ranking(text)[:top_k]
        else:  # fusion — RRF(k=60) over full bm25 + dense rankings
            bm25_full = await self._bm25.retrieve(query, intent=intent, top_k=len(self.chunks))
            rank_b = [c.policy_id for c in bm25_full]
            rank_d = self._dense_ranking(text)
            scores: dict[str, float] = {}
            for ranking in (rank_b, rank_d):
                for pos, pid in enumerate(ranking, 1):
                    scores[pid] = scores.get(pid, 0.0) + 1.0 / (60 + pos)
            top = sorted(scores, key=lambda p: (-scores[p], p))[:top_k]
        return [
            RetrievedChunk(
                policy_id=pid,
                title=self._by_id[pid]["title"],
                content=self._by_id[pid]["content"],
                score=0.0,
                category=self._by_id[pid].get("category", ""),
                tags=self._by_id[pid].get("tags", []),
            )
            for pid in top
        ]


# ---------------------------------------------------------------------------
# Draft generation
# ---------------------------------------------------------------------------
async def generate_drafts(model: str, backend: str) -> None:
    settings.LOCAL_MODEL_BASE_URL = "https://api.openai.com/v1"
    settings.LOCAL_MODEL_NAME = model
    settings.LOCAL_MODEL_API_KEY = read_key()
    # Reasoning models spend completion budget on reasoning tokens before any
    # visible text; the production default (500) would come back empty.
    settings.DRAFTER_MAX_TOKENS = 2500

    rows = load_answerable()
    done = set()
    if DRAFTS_PATH.exists():
        done = {
            (d["ticket_id"], d["config"])
            for d in map(json.loads, open(DRAFTS_PATH, encoding="utf-8"))
        }
    retriever = Retriever(backend)
    router = EmailRouter(strategy="rule_based")
    sem = asyncio.Semaphore(CONCURRENCY)
    lock = asyncio.Lock()
    print(f"drafts: rows={len(rows)} configs={list(GUIDE_CONFIGS)} ({len(done)} already done)")

    async def one(row: dict, config: str) -> None:
        # Raw ticket text, deliberately unscrubbed: the eval must exercise the
        # system on exactly what requesters wrote (names, addresses included).
        email = {
            "from": "requester@example.org",
            "subject": row["subject"],
            "body": row["question"],
        }
        classification = keyword_classify(email["subject"], email["body"])
        # Ablation-winning retrieval config (real_eval report): subject included
        # in the query, NO intent token. Deliberately differs from today's
        # orchestrator (body[:300] + intent) — this is the recommended config.
        chunks = await retriever.retrieve(
            f"{email['subject']} {email['body'][:300]}",
            "",
            settings.MAX_RETRIEVED_CHUNKS,
        )
        routing = router.route(classification, chunks)
        async with sem:
            draft = await ResponseDrafter(provider="local").draft(
                email, classification, chunks, routing
            )
        rec = {
            "ticket_id": row["ticket_id"],
            "config": config,
            "draft_text": draft.draft_text,
            "citations": draft.citations,
            "model_used": draft.model_used,
            "error": draft.generation_metadata.get("error"),
            "retrieved_ids": [c.policy_id for c in chunks],
            "gold": row["gold"],
            "lane": routing.lane,
            "kw_intent": classification.intent,
        }
        async with lock:
            with open(DRAFTS_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # One config at a time: STYLE_GUIDE_PATH is process-global settings state,
    # so it is set once per batch and only that batch's drafts run concurrently.
    for config in GUIDE_CONFIGS:
        batch = [r for r in rows if (r["ticket_id"], config) not in done]
        if not batch:
            print(f"  config={config}: nothing to do")
            continue
        settings.STYLE_GUIDE_PATH = GUIDE_CONFIGS[config]
        await asyncio.gather(*(one(r, config) for r in batch))
        print(f"  config={config}: {len(batch)} drafts done", flush=True)
    print(f"drafts -> {DRAFTS_PATH}")


# ---------------------------------------------------------------------------
# Blinded judge packets
# ---------------------------------------------------------------------------
def build_judge_packets(per_batch: int) -> None:
    samples = {r["ticket_id"]: r for r in map(json.loads, open(SAMPLE_PATH, encoding="utf-8"))}
    chunks = {c["id"]: c for c in json.load(open(KB_PATH, encoding="utf-8"))}
    drafts: dict[int, dict[str, dict]] = {}
    for d in map(json.loads, open(DRAFTS_PATH, encoding="utf-8")):
        if not d.get("error"):
            drafts.setdefault(d["ticket_id"], {})[d["config"]] = d

    complete = [t for t, cfgs in drafts.items() if len(cfgs) == len(GUIDE_CONFIGS)]
    complete.sort()
    PACKET_DIR.mkdir(parents=True, exist_ok=True)
    letters = ["A", "B", "C"][: len(GUIDE_CONFIGS)]
    n_batches = 0
    for start in range(0, len(complete), per_batch):
        items = []
        for tid in complete[start : start + per_batch]:
            s = samples[tid]
            cfgs = list(GUIDE_CONFIGS)
            rot = tid % len(cfgs)  # deterministic blinding rotation
            order = cfgs[rot:] + cfgs[:rot]
            d0 = drafts[tid][order[0]]
            items.append(
                {
                    "ticket_id": tid,
                    "inquiry_subject": s["subject"],
                    "inquiry_body": s["question"][:1500],
                    "reference_reply": s["reply"][:1500],
                    "retrieved_policy_context": [
                        {"id": cid, "title": chunks[cid]["title"], "content": chunks[cid]["content"]}
                        for cid in d0["retrieved_ids"]
                        if cid in chunks
                    ],
                    "drafts": {
                        letters[i]: drafts[tid][cfg]["draft_text"]
                        for i, cfg in enumerate(order)
                    },
                    "_key": {letters[i]: cfg for i, cfg in enumerate(order)},
                }
            )
        packet = PACKET_DIR / f"packet_{n_batches:02d}.json"
        packet.write_text(json.dumps(items, indent=1, ensure_ascii=False))
        n_batches += 1
    print(f"packets: {n_batches} x <= {per_batch} tickets -> {PACKET_DIR}")
    print("NOTE: '_key' maps letters->configs; judges must never see it. "
          "Judge prompts should receive the packet with _key stripped.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stage", choices=["drafts", "packets"])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--backend", choices=["dense", "bm25", "fusion"], default="fusion")
    parser.add_argument("--per-batch", type=int, default=12)
    args = parser.parse_args()
    if args.stage == "drafts":
        asyncio.run(generate_drafts(args.model, args.backend))
    else:
        build_judge_packets(args.per_batch)


if __name__ == "__main__":
    main()
