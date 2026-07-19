"""E005 — embed-representation A/B (retrieval only, no model calls at run time).

Question: does dropping the repeated doc-prefix from the EMBEDDED title improve
retrieval on real tickets, as the §2b confusability probe predicts?

Arms (dense embed string only; BM25 unchanged = full title+content+tags):
  full     "{title} {content}"        current production (faiss_retriever.py:100)
  leaf     "{leaf} {content}"          drop doc-prefix: leaf = title after first ' — '
  content  "{content}"                 reference lower bound

Config mirrors E003's winner: distilled-joined queries (data/eval_real/
distilled_queries.jsonl), no intent token, fusion = RRF(k=60) over BM25+dense.
Metrics = bench_real.score_ranking (multi-gold hit@k/recall@k/ndcg@k, k=1,3,5),
reused verbatim so numbers are comparable to E001/E003. Also MRR + mean best-gold
rank (more sensitive than the hit@3 threshold at n=37).

The 'full' + fusion arm should reproduce E003's distilled-joined row
(hit@3 .892, recall@3 .649) — a sanity check the harness matches before trusting
the leaf/content deltas.

Usage:  cd backend && OMP_NUM_THREADS=4 HF_HUB_OFFLINE=1 python scripts/e005_embed_repr.py
"""
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from scripts.bench_real import score_ranking, KS, RRF_K  # noqa: E402  (reuse exact metrics)
from scripts._kb_retriever import build_retriever_from_kb  # noqa: E402

KB_PATH = REPO_ROOT / "data" / "knowledge_base" / "policies.json"
EVAL_DIR = REPO_ROOT / "data" / "eval_real"
SAMPLE_PATH = EVAL_DIR / "sample.jsonl"
LABELS_PATH = EVAL_DIR / "labels.jsonl"
QUERIES_PATH = EVAL_DIR / "distilled_queries.jsonl"

leaf = lambda title: " — ".join(title.split(" — ")[1:]) or title

EMBED = {
    "full":    lambda c: f"{c['title']} {c['content']}",
    "leaf":    lambda c: f"{leaf(c['title'])} {c['content']}",
    "content": lambda c: c["content"],
}


def load_answerable() -> list[dict]:
    # replicated from draft_eval.load_answerable (same 37-ticket set as E003)
    samples = {r["ticket_id"]: r for r in map(json.loads, open(SAMPLE_PATH, encoding="utf-8"))}
    rows = []
    for line in open(LABELS_PATH, encoding="utf-8"):
        l = json.loads(line)
        if l["policy_answerable"] and l["relevant_chunk_ids"] and l["ticket_id"] in samples:
            rows.append({**samples[l["ticket_id"]], "gold": l["relevant_chunk_ids"]})
    return rows


def rrf(rank_a, rank_b):
    scores = {}
    for ranking in (rank_a, rank_b):
        for pos, pid in enumerate(ranking, 1):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + pos)
    return sorted(scores, key=lambda p: (-scores[p], p))


def best_gold_rank(ranking, gold):
    for i, pid in enumerate(ranking, 1):
        if pid in gold:
            return i
    return None  # no gold retrieved at all


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


async def main():
    import numpy as np
    from sentence_transformers import SentenceTransformer

    chunks = json.load(open(KB_PATH, encoding="utf-8"))
    ids = [c["id"] for c in chunks]
    rows = load_answerable()
    distilled = {r["ticket_id"]: r["queries"]
                 for r in map(json.loads, open(QUERIES_PATH, encoding="utf-8"))}
    missing = [r["ticket_id"] for r in rows if not distilled.get(r["ticket_id"])]
    print(f"tickets: {len(rows)}   missing distilled queries: {missing or 'none'}")

    embedder = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    doc_vecs = {arm: embedder.encode([fn(c) for c in chunks], normalize_embeddings=True)
                for arm, fn in EMBED.items()}

    bm25 = build_retriever_from_kb(KB_PATH)

    # Per-ticket: BM25 full ranking (arm-independent) + query vector, cached.
    queries = {r["ticket_id"]: " ".join(distilled.get(r["ticket_id"]) or [r["subject"]]) for r in rows}
    bm25_rank = {}
    for r in rows:
        res = await bm25.retrieve(queries[r["ticket_id"]], intent="", top_k=len(ids))
        bm25_rank[r["ticket_id"]] = [c.policy_id for c in res]
    qvecs = embedder.encode([queries[r["ticket_id"]] for r in rows], normalize_embeddings=True)

    def dense_rank(arm, qv):
        order = np.argsort(-(doc_vecs[arm] @ qv))
        return [ids[i] for i in order]

    results = {}
    for arm in EMBED:
        for backend in ("dense", "fusion"):
            srows, rr, gold_ranks, misses = [], [], [], 0
            for qi, r in enumerate(rows):
                gold = set(r["gold"])
                dr = dense_rank(arm, qvecs[qi])
                ranking = dr if backend == "dense" else rrf(bm25_rank[r["ticket_id"]], dr)
                srows.append(score_ranking(ranking, gold))
                gr = best_gold_rank(ranking, gold)
                if gr is None:
                    misses += 1
                else:
                    rr.append(1.0 / gr)
                    gold_ranks.append(gr)
            agg = {k: round(mean([s[k] for s in srows]), 3) for k in srows[0]}
            agg["mrr"] = round(mean(rr + [0.0] * misses), 3)
            agg["mean_gold_rank"] = round(mean(gold_ranks), 1) if gold_ranks else None
            agg["median_gold_rank"] = sorted(gold_ranks)[len(gold_ranks) // 2] if gold_ranks else None
            results[(arm, backend)] = agg

    # ---- print ----
    cols = ["hit@1", "hit@3", "hit@5", "recall@3", "recall@5", "ndcg@3", "mrr", "mean_gold_rank"]
    print(f"\n{'arm':9s} {'backend':7s} " + " ".join(f"{c:>8s}" for c in cols))
    for backend in ("dense", "fusion"):
        for arm in EMBED:
            a = results[(arm, backend)]
            print(f"{arm:9s} {backend:7s} " + " ".join(f"{str(a[c]):>8s}" for c in cols))

    print("\nΔ vs 'full' (same backend):")
    for backend in ("dense", "fusion"):
        base = results[("full", backend)]
        for arm in ("leaf", "content"):
            a = results[(arm, backend)]
            d = {c: round(a[c] - base[c], 3) for c in ["hit@3", "hit@5", "recall@3", "ndcg@3", "mrr"]}
            print(f"  {arm:8s} {backend:7s} " + "  ".join(f"{k} {v:+.3f}" for k, v in d.items()))

    out = REPO_ROOT / "backend" / "reports" / "e005_embed_repr.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({f"{a}|{b}": v for (a, b), v in results.items()}, indent=2))
    print(f"\nreport -> {out}  (n={len(rows)})")


if __name__ == "__main__":
    asyncio.run(main())
