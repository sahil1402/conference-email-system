"""E008 — embedding-model A/B: MiniLM vs BGE (retrieval only, no LLM at run time).

Question: does swapping the dense embedder from all-MiniLM-L6-v2 (2021-era, 384-d)
to a modern BGE model improve real-ticket retrieval? MiniLM is the current default
(faiss_retriever.py, config.FAISS_MODEL_NAME) and the known weak link.

Arms (dense embedder only; BM25 unchanged and model-independent):
  minilm     all-MiniLM-L6-v2         current production (384-d)
  bge-small  BAAI/bge-small-en-v1.5   size-matched upgrade (384-d)
  bge-base   BAAI/bge-base-en-v1.5    the real upgrade (768-d)

Doc text = production leaf-title representation ("{leaf} {content}", E005). BGE
needs a QUERY instruction ("Represent this sentence for searching relevant
passages: "); passages get none. Config mirrors E005/E007: distilled-joined queries,
fusion = RRF(k=60) over BM25 + dense. Metrics = bench_real.score_ranking
(multi-gold hit@k/recall@k/ndcg@k) + MRR + mean/median best-gold rank.

Sanity: minilm|fusion should reproduce E005/E007 leaf|fusion (hit@1 .703, hit@3 .892,
hit@5 .919, ndcg@3 .638, mrr .800, mean gold rank 2.1).

Usage (first run downloads BGE — needs network, so NOT HF_HUB_OFFLINE):
    cd backend
    export PATH=/u/jpang1/miniconda3/envs/autoexp/bin:$PATH
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=4 python scripts/e008_embed_model_ablation.py
"""
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from scripts.bench_real import score_ranking, RRF_K  # noqa: E402
from scripts._kb_retriever import build_retriever_from_kb  # noqa: E402

KB_PATH = REPO_ROOT / "data" / "knowledge_base" / "policies.json"
EVAL = REPO_ROOT / "data" / "eval_real"

_BGE_Q = "Represent this sentence for searching relevant passages: "
ARMS = {
    "minilm":    {"model": "all-MiniLM-L6-v2",       "qprefix": ""},
    "bge-small": {"model": "BAAI/bge-small-en-v1.5",  "qprefix": _BGE_Q},
    "bge-base":  {"model": "BAAI/bge-base-en-v1.5",   "qprefix": _BGE_Q},
}
leaf = lambda t: " — ".join(t.split(" — ")[1:]) or t


def load_answerable():
    samples = {r["ticket_id"]: r for r in map(json.loads, open(EVAL / "sample.jsonl", encoding="utf-8"))}
    rows = []
    for line in open(EVAL / "labels.jsonl", encoding="utf-8"):
        l = json.loads(line)
        if l["policy_answerable"] and l["relevant_chunk_ids"] and l["ticket_id"] in samples:
            rows.append({**samples[l["ticket_id"]], "gold": l["relevant_chunk_ids"]})
    return rows


def rrf(a, b):
    s = {}
    for r in (a, b):
        for pos, pid in enumerate(r, 1):
            s[pid] = s.get(pid, 0.0) + 1.0 / (RRF_K + pos)
    return sorted(s, key=lambda p: (-s[p], p))


def best_rank(ranking, gold):
    for i, pid in enumerate(ranking, 1):
        if pid in gold:
            return i
    return None


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


async def main():
    import numpy as np
    from sentence_transformers import SentenceTransformer

    chunks = json.load(open(KB_PATH, encoding="utf-8"))
    ids = [c["id"] for c in chunks]
    doc_texts = [f"{leaf(c['title'])} {c['content']}" for c in chunks]
    rows = load_answerable()
    distilled = {r["ticket_id"]: r["queries"] for r in map(json.loads, open(EVAL / "distilled_queries.jsonl", encoding="utf-8"))}
    queries = {r["ticket_id"]: " ".join(distilled.get(r["ticket_id"]) or [r["subject"]]) for r in rows}
    print(f"tickets: {len(rows)}   chunks: {len(chunks)}")

    # BM25 ranking (model-independent) — compute once.
    bm25 = build_retriever_from_kb(KB_PATH)
    bm25_rank = {}
    for r in rows:
        res = await bm25.retrieve(queries[r["ticket_id"]], intent="", top_k=len(ids))
        bm25_rank[r["ticket_id"]] = [c.policy_id for c in res]

    results = {}
    for arm, cfg in ARMS.items():
        emb = SentenceTransformer(cfg["model"], device="cpu")
        dvecs = emb.encode(doc_texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
        qvecs = emb.encode([cfg["qprefix"] + queries[r["ticket_id"]] for r in rows],
                           normalize_embeddings=True, batch_size=32, show_progress_bar=False)
        for backend in ("dense", "fusion"):
            srows, rr, granks, misses = [], [], [], 0
            for qi, r in enumerate(rows):
                gold = set(r["gold"])
                dr = [ids[i] for i in np.argsort(-(dvecs @ qvecs[qi]))]
                ranking = dr if backend == "dense" else rrf(bm25_rank[r["ticket_id"]], dr)
                srows.append(score_ranking(ranking, gold))
                gr = best_rank(ranking, gold)
                if gr is None:
                    misses += 1
                else:
                    rr.append(1.0 / gr); granks.append(gr)
            agg = {k: round(mean([s[k] for s in srows]), 3) for k in srows[0]}
            agg["mrr"] = round(mean(rr + [0.0] * misses), 3)
            agg["mean_gold_rank"] = round(mean(granks), 1) if granks else None
            results[(arm, backend)] = agg
        print(f"  {arm} done ({cfg['model']})")

    cols = ["hit@1", "hit@3", "hit@5", "recall@3", "recall@5", "ndcg@3", "mrr", "mean_gold_rank"]
    print(f"\n{'arm':10s} {'backend':7s} " + " ".join(f"{c:>8s}" for c in cols))
    for backend in ("dense", "fusion"):
        for arm in ARMS:
            a = results[(arm, backend)]
            print(f"{arm:10s} {backend:7s} " + " ".join(f"{str(a[c]):>8s}" for c in cols))

    print("\nΔ vs minilm (same backend):")
    for backend in ("dense", "fusion"):
        base = results[("minilm", backend)]
        for arm in ("bge-small", "bge-base"):
            a = results[(arm, backend)]
            d = {c: round(a[c] - base[c], 3) for c in ["hit@1", "hit@3", "hit@5", "recall@3", "ndcg@3", "mrr"]}
            print(f"  {arm:10s} {backend:7s} " + "  ".join(f"{k}{v:+.3f}" for k, v in d.items()))

    print("\nSanity minilm|fusion vs E005 leaf|fusion (hit@1 .703, hit@3 .892, mrr .800):")
    a = results[("minilm", "fusion")]
    print(f"  got hit@1 {a['hit@1']}, hit@3 {a['hit@3']}, hit@5 {a['hit@5']}, mrr {a['mrr']}, rank {a['mean_gold_rank']}")

    out = REPO_ROOT / "backend" / "reports" / "e008_embed_model_ablation.json"
    out.write_text(json.dumps({f"{a}|{b}": v for (a, b), v in results.items()}, indent=2))
    print(f"\nreport -> {out}  (n={len(rows)})")


if __name__ == "__main__":
    asyncio.run(main())
