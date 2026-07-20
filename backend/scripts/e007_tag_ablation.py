"""E007 — policy-tag ablation (retrieval only, no model calls at run time).

Question: do the auto-generated chunk `tags` earn their place in the BM25
document string, or are they redundant noise that can be dropped?

Background (2026-07-19 audit, docs/local/CLASSIFICATION_REWORK.md §B4): tags are
built by chunk_policies.py from (a) per-doc YAML frontmatter + (b) `heading_tags`
= lowercased content-words of each section heading. They are indexed ONLY by BM25
(retriever.py:82, appended to `title + content`); FAISS ignores them (E005 embeds
leaf-title + content). `heading_tags` largely duplicate words already in the chunk
title, so the hypothesis is that dropping tags leaves retrieval ~flat.

Arms (BM25 corpus only; dense side is held at PRODUCTION = leaf-title embed, which
tags never touch — so dense is identical across arms and fusion moves only through
its BM25 half):
  tags_on   BM25 over "title + content + tags"   (current production)
  tags_off  BM25 over "title + content"          (tags stripped)

Config mirrors E005's production-parity setup: distilled-joined queries
(data/eval_real/distilled_queries.jsonl), no intent token, fusion = RRF(k=60) over
BM25 + dense. Metrics = bench_real.score_ranking (multi-gold hit@k/recall@k/ndcg@k,
k=1,3,5) reused verbatim, plus MRR + mean/median best-gold rank (more sensitive
than the hit@3 threshold at n=37).

Sanity check: the `tags_on | fusion` arm should reproduce E005's `leaf | fusion`
row (hit@1 .703, hit@3 .892, hit@5 .919, recall@3 .640, ndcg@3 .638, mrr .800,
mean gold rank 2.1) — confirming the harness matches before trusting the delta.

Usage:
    cd backend
    export PATH=/u/jpang1/miniconda3/envs/autoexp/bin:$PATH
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
      RAYON_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false HF_HUB_OFFLINE=1 \
      python scripts/e007_tag_ablation.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from scripts.bench_real import score_ranking, RRF_K  # noqa: E402  (exact metrics)
from scripts._kb_retriever import build_retriever_from_kb  # noqa: E402

KB_PATH = REPO_ROOT / "data" / "knowledge_base" / "policies.json"
EVAL_DIR = REPO_ROOT / "data" / "eval_real"
SAMPLE_PATH = EVAL_DIR / "sample.jsonl"
LABELS_PATH = EVAL_DIR / "labels.jsonl"
QUERIES_PATH = EVAL_DIR / "distilled_queries.jsonl"

# Production dense representation (E005 decision): leaf title = drop the "<Doc> — "
# prefix, then " content". Held fixed across arms — tags never enter the dense side.
leaf = lambda title: " — ".join(title.split(" — ")[1:]) or title


def load_answerable() -> list[dict]:
    """The same 37-ticket answerable set E003/E005 used."""
    samples = {r["ticket_id"]: r for r in map(json.loads, open(SAMPLE_PATH, encoding="utf-8"))}
    rows = []
    for line in open(LABELS_PATH, encoding="utf-8"):
        l = json.loads(line)
        if l["policy_answerable"] and l["relevant_chunk_ids"] and l["ticket_id"] in samples:
            rows.append({**samples[l["ticket_id"]], "gold": l["relevant_chunk_ids"]})
    return rows


def write_tagless_kb(chunks: list[dict]) -> Path:
    """Write a temp KB JSON identical to production but with tags emptied."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="kb_notags_")
    os.close(fd)
    stripped = [{**c, "tags": []} for c in chunks]
    Path(path).write_text(json.dumps(stripped), encoding="utf-8")
    return Path(path)


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
    return None


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


async def bm25_rankings(retriever, rows, queries, n_ids):
    out = {}
    for r in rows:
        res = await retriever.retrieve(queries[r["ticket_id"]], intent="", top_k=n_ids)
        out[r["ticket_id"]] = [c.policy_id for c in res]
    return out


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

    # Distilled-joined query per ticket (fallback: subject), same as E005.
    queries = {r["ticket_id"]: " ".join(distilled.get(r["ticket_id"]) or [r["subject"]]) for r in rows}

    # --- dense side: production leaf-title embed, identical across arms ---
    embedder = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    doc_vecs = embedder.encode([f"{leaf(c['title'])} {c['content']}" for c in chunks],
                               normalize_embeddings=True)
    qvecs = embedder.encode([queries[r["ticket_id"]] for r in rows], normalize_embeddings=True)
    dense_rank = {r["ticket_id"]: [ids[i] for i in np.argsort(-(doc_vecs @ qvecs[qi]))]
                  for qi, r in enumerate(rows)}

    # --- BM25 side: two corpora (tags on / off) ---
    bm25_on = build_retriever_from_kb(KB_PATH)
    tagless_path = write_tagless_kb(chunks)
    bm25_off = build_retriever_from_kb(tagless_path)
    bm25_rank = {
        "tags_on": await bm25_rankings(bm25_on, rows, queries, len(ids)),
        "tags_off": await bm25_rankings(bm25_off, rows, queries, len(ids)),
    }

    # --- score both arms for bm25 and fusion ---
    results = {}
    for arm in ("tags_on", "tags_off"):
        for backend in ("bm25", "fusion"):
            srows, rr, gold_ranks, misses = [], [], [], 0
            for r in rows:
                gold = set(r["gold"])
                br = bm25_rank[arm][r["ticket_id"]]
                ranking = br if backend == "bm25" else rrf(br, dense_rank[r["ticket_id"]])
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

    # How many chunks' BM25 doc string actually changed (had >=1 tag)?
    n_tagged = sum(1 for c in chunks if c.get("tags"))

    # ---- print ----
    cols = ["hit@1", "hit@3", "hit@5", "recall@3", "recall@5", "ndcg@3", "mrr", "mean_gold_rank"]
    print(f"\nchunks with >=1 tag (BM25 doc changed): {n_tagged}/{len(chunks)}")
    print(f"\n{'arm':9s} {'backend':7s} " + " ".join(f"{c:>8s}" for c in cols))
    for backend in ("bm25", "fusion"):
        for arm in ("tags_on", "tags_off"):
            a = results[(arm, backend)]
            print(f"{arm:9s} {backend:7s} " + " ".join(f"{str(a[c]):>8s}" for c in cols))

    print("\nΔ tags_off − tags_on (same backend):")
    for backend in ("bm25", "fusion"):
        on, off = results[("tags_on", backend)], results[("tags_off", backend)]
        d = {c: round(off[c] - on[c], 3) for c in ["hit@1", "hit@3", "hit@5", "recall@3", "ndcg@3", "mrr"]}
        print(f"  {backend:7s} " + "  ".join(f"{k} {v:+.3f}" for k, v in d.items()))

    print("\nSanity — 'tags_on | fusion' vs E005 'leaf | fusion' "
          "(expect hit@1 .703, hit@3 .892, hit@5 .919, ndcg@3 .638, mrr .800, rank 2.1):")
    a = results[("tags_on", "fusion")]
    print(f"  got: hit@1 {a['hit@1']}, hit@3 {a['hit@3']}, hit@5 {a['hit@5']}, "
          f"ndcg@3 {a['ndcg@3']}, mrr {a['mrr']}, mean_gold_rank {a['mean_gold_rank']}")

    out = REPO_ROOT / "backend" / "reports" / "e007_tag_ablation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"n_tickets": len(rows), "n_chunks": len(chunks), "n_tagged": n_tagged,
         "results": {f"{a}|{b}": v for (a, b), v in results.items()}}, indent=2))
    print(f"\nreport -> {out}  (n={len(rows)})")


if __name__ == "__main__":
    asyncio.run(main())
