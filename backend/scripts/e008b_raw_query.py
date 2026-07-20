"""E008b — embedder A/B on RAW vs DISTILLED queries (retrieval only).

Follow-up to E008: E008 tested only distilled queries, which favour BM25/MiniLM and
don't exercise BGE's natural-language strength. This gives each embedder the RAW
email (subject + body) as the query — BGE's fair test — alongside the distilled
baseline. For each query mode, BM25 uses that same query (the real pipeline for
that mode), so fusion is apples-to-apples.

Arms: minilm (all-MiniLM-L6-v2) vs bge-base (BAAI/bge-base-en-v1.5).
Query modes: distilled (E003 cache) vs raw (subject + question).
Metrics: bench_real.score_ranking + MRR + mean gold rank, n=37 gold.

Usage: cd backend && OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=4 python scripts/e008b_raw_query.py
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
ARMS = {"minilm": {"model": "all-MiniLM-L6-v2", "qprefix": ""},
        "bge-base": {"model": "BAAI/bge-base-en-v1.5", "qprefix": _BGE_Q}}
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

    qmodes = {
        "distilled": {r["ticket_id"]: " ".join(distilled.get(r["ticket_id"]) or [r["subject"]]) for r in rows},
        "raw":       {r["ticket_id"]: f"{r['subject']} {r['question']}".strip() for r in rows},
    }
    print(f"tickets: {len(rows)}")

    # BM25 ranking per query mode (BM25 uses that mode's query).
    bm25 = build_retriever_from_kb(KB_PATH)
    bm25_rank = {}
    for mode, q in qmodes.items():
        bm25_rank[mode] = {}
        for r in rows:
            res = await bm25.retrieve(q[r["ticket_id"]], intent="", top_k=len(ids))
            bm25_rank[mode][r["ticket_id"]] = [c.policy_id for c in res]

    results = {}
    for arm, cfg in ARMS.items():
        emb = SentenceTransformer(cfg["model"], device="cpu")
        dvecs = emb.encode(doc_texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
        for mode, q in qmodes.items():
            qvecs = emb.encode([cfg["qprefix"] + q[r["ticket_id"]] for r in rows],
                               normalize_embeddings=True, batch_size=32, show_progress_bar=False)
            for backend in ("dense", "fusion"):
                srows, rr, granks, misses = [], [], [], 0
                for qi, r in enumerate(rows):
                    gold = set(r["gold"])
                    dr = [ids[i] for i in np.argsort(-(dvecs @ qvecs[qi]))]
                    ranking = dr if backend == "dense" else rrf(bm25_rank[mode][r["ticket_id"]], dr)
                    srows.append(score_ranking(ranking, gold))
                    gr = best_rank(ranking, gold)
                    if gr is None:
                        misses += 1
                    else:
                        rr.append(1.0 / gr); granks.append(gr)
                agg = {k: round(mean([s[k] for s in srows]), 3) for k in srows[0]}
                agg["mrr"] = round(mean(rr + [0.0] * misses), 3)
                agg["mean_gold_rank"] = round(mean(granks), 1) if granks else None
                results[(arm, mode, backend)] = agg
        print(f"  {arm} done")

    cols = ["hit@1", "hit@3", "hit@5", "recall@3", "ndcg@3", "mrr", "mean_gold_rank"]
    print(f"\n{'arm':9s} {'qmode':10s} {'backend':7s} " + " ".join(f"{c:>7s}" for c in cols))
    for mode in ("distilled", "raw"):
        for backend in ("dense", "fusion"):
            for arm in ARMS:
                a = results[(arm, mode, backend)]
                print(f"{arm:9s} {mode:10s} {backend:7s} " + " ".join(f"{str(a[c]):>7s}" for c in cols))

    print("\nΔ bge-base − minilm (same qmode, backend):")
    for mode in ("distilled", "raw"):
        for backend in ("dense", "fusion"):
            b, m = results[("bge-base", mode, backend)], results[("minilm", mode, backend)]
            d = {c: round(b[c] - m[c], 3) for c in ["hit@1", "hit@3", "recall@3", "ndcg@3", "mrr"]}
            print(f"  {mode:10s} {backend:7s} " + "  ".join(f"{k}{v:+.3f}" for k, v in d.items()))

    out = REPO_ROOT / "backend" / "reports" / "e008b_raw_query.json"
    out.write_text(json.dumps({f"{a}|{q}|{b}": v for (a, q, b), v in results.items()}, indent=2))
    print(f"\nreport -> {out}  (n={len(rows)})")


if __name__ == "__main__":
    asyncio.run(main())
