"""E010 — intent-prior ablation (retrieval only, no model calls at run time).

Question: B5 added a soft additive intent-prior boost inside
``FusionRetriever.retrieve()`` (``fusion_retriever.py:INTENT_PRIOR_BOOST``) — when
the email's intent is present in a chunk's ``intents`` list, that chunk's fused RRF
score gets ``INTENT_PRIOR_BOOST`` added (a full single-ranker rank-1 RRF vote,
``1/(k+1)`` ≈ 0.0164 at k=60), then the fused ranking is re-sorted. Does this
measurably help retrieval on the 37-gold real-ticket set?

Arms:
  prior_off   current baseline — no boost (pre-B5 behaviour)
  prior_on    B5's boost applied post-fusion, using each ticket's mapped intent

Per-ticket intent for the ON arm (documented choice): no cached distiller output in
the NEW 14-intent taxonomy exists for this eval set (checked data/eval_real/*.jsonl —
only the OLD 11-intent gold `intent` field and OLD `kw_intent` are cached; re-running
the distiller live would need a model call, out of scope for a retrieval-only
harness). So we map each ticket's GOLD `intent` field (old taxonomy, from
labels.jsonl) through the Task-B6-brief's old->new table (``OLD_TO_NEW`` below) onto
the new taxonomy (`app.pipeline.taxonomy.VALID_INTENTS`). CAVEAT: this is a clean
upper-bound proxy, not production behaviour — a live classification would be noisier
(the classifier/distiller mis-fires sometimes), and a wrong/noisy intent WEAKENS the
prior (it may boost the wrong chunks or boost nothing at all). "other" (2/37 tickets)
isn't one of the old 11 at all; there is no safe mapping, so it falls back to the
taxonomy's own fallback intent (`cms_support`) — a deliberately weak/no-op prior for
those two tickets, same as `general_inquiry`.

Backends: dense (MiniLM, production leaf-title embed, E005) + fusion (RRF k=60 over
BM25 + dense, current production BM25 corpus post-E007 tag-drop). BM25-alone and
dense-alone are intentionally NOT boosted in either arm — the boost is fusion-only in
production (see faiss_retriever.py / retriever.py docstrings: "the soft intent prior
is a *fusion-only* score boost (B5)... so B6 can ablate the boost cleanly"). So dense
is IDENTICAL between prior_off/prior_on BY DESIGN — not a harness bug, it mirrors
production exactly. Fusion is therefore the one arm this ablation actually tests;
dense is reported for completeness/symmetry with E007/E008, and to make that
"unaffected" claim visible in the numbers rather than just asserted.

Chunk `intents` (which chunks can answer which intents, B3-labeled) are read
straight from data/knowledge_base/policies.json (49/93 chunks currently non-empty).

Config mirrors E007/E008: distilled-joined queries, production leaf-title dense
embed, fusion = RRF(k=60) over BM25 + dense. Metrics = bench_real.score_ranking
(multi-gold hit@k/recall@k/ndcg@k, k=1,3,5) + MRR + mean best-gold rank.

Sanity check: prior_off | fusion should reproduce E007's tags_off|fusion / E008's
minilm|fusion row (current production, pre-B5): hit@1 .730, hit@3 .892, hit@5 .919,
recall@3 .640, ndcg@3 .645, mrr .814, mean gold rank 2.0.

Usage:
    cd backend
    export PATH=/u/jpang1/miniconda3/envs/autoexp/bin:$PATH
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=4 MKL_NUM_THREADS=1 \
      TOKENIZERS_PARALLELISM=false HF_HUB_OFFLINE=1 \
      python scripts/e010_intent_prior_ablation.py
"""
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.pipeline.fusion_retriever import INTENT_PRIOR_BOOST  # noqa: E402
from app.pipeline.taxonomy import VALID_INTENTS, FALLBACK_INTENT  # noqa: E402
from scripts.bench_real import score_ranking, RRF_K  # noqa: E402  (exact metrics)
from scripts._kb_retriever import build_retriever_from_kb  # noqa: E402

KB_PATH = REPO_ROOT / "data" / "knowledge_base" / "policies.json"
EVAL_DIR = REPO_ROOT / "data" / "eval_real"
SAMPLE_PATH = EVAL_DIR / "sample.jsonl"
LABELS_PATH = EVAL_DIR / "labels.jsonl"
QUERIES_PATH = EVAL_DIR / "distilled_queries.jsonl"

# Production dense representation (E005 decision), held fixed — identical across arms.
leaf = lambda title: " — ".join(title.split(" — ")[1:]) or title

# Old (11-intent) -> new (14-intent) mapping, per the Task-B6 brief header (mirrors
# the Task A6 test-sweep table). "closest new (default)" column; per-ticket rationale
# text in labels.jsonl was hand-checked for the 37-gold set and confirms the default
# fits every ticket actually present (no ticket needed the appeal-flavoured
# `review_decision_appeal` alternative for `ethics_concern` — all 7 are multi-submission
# / anonymity / misconduct *violation reports*, matching `anonymity_violation`).
OLD_TO_NEW = {
    "submission_deadline": "submission_requirements",
    "formatting_requirements": "submission_format_policy",
    "general_inquiry": FALLBACK_INTENT,  # catch-all/fallback in the new taxonomy too
    "review_assignment": "reviewer_assignment",
    "authorship_dispute": "author_list_change",  # real traffic = admin author-list edits
    "submission_withdrawal": "submission_upload_help",  # real traffic = restore-withdrawn
    "ethics_concern": "anonymity_violation",  # integrity-report default (see docstring)
    "technical_issue": "review_submission_help",
    "sponsorship": FALLBACK_INTENT,
    "publicity": FALLBACK_INTENT,
    "media_inquiry": FALLBACK_INTENT,
    # "other": not one of the old 11 (labeler catch-all for "fits none") — no safe
    # mapping exists, so use the taxonomy's own fallback (documented caveat above).
    "other": FALLBACK_INTENT,
}
assert set(OLD_TO_NEW.values()) <= set(VALID_INTENTS) | {FALLBACK_INTENT}


def load_answerable() -> list[dict]:
    """The same 37-ticket answerable set E003/E005/E007/E008 used, plus the gold
    OLD-taxonomy `intent` field (needed here, unlike the earlier harnesses)."""
    samples = {r["ticket_id"]: r for r in map(json.loads, open(SAMPLE_PATH, encoding="utf-8"))}
    rows = []
    for line in open(LABELS_PATH, encoding="utf-8"):
        l = json.loads(line)
        if l["policy_answerable"] and l["relevant_chunk_ids"] and l["ticket_id"] in samples:
            rows.append({**samples[l["ticket_id"]], "gold": l["relevant_chunk_ids"], "old_intent": l["intent"]})
    return rows


def fuse(rank_a: list[str], rank_b: list[str], *, prior_intent: str = "", chunk_intents=None) -> list[str]:
    """RRF fuse two full rankings, optionally applying B5's post-fusion intent boost.

    Mirrors ``FusionRetriever.retrieve`` exactly: additive boost, then re-sort by
    (-score, policy_id) for a stable deterministic order.
    """
    scores: dict[str, float] = {}
    for ranking in (rank_a, rank_b):
        for pos, pid in enumerate(ranking, 1):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + pos)
    boosted = 0
    if prior_intent:
        for pid in scores:
            if prior_intent in (chunk_intents.get(pid) or set()):
                scores[pid] += INTENT_PRIOR_BOOST
                boosted += 1
    return sorted(scores, key=lambda p: (-scores[p], p)), boosted


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
    chunk_intents = {c["id"]: set(c.get("intents") or []) for c in chunks}
    n_with_intents = sum(1 for s in chunk_intents.values() if s)

    rows = load_answerable()
    distilled = {r["ticket_id"]: r["queries"]
                 for r in map(json.loads, open(QUERIES_PATH, encoding="utf-8"))}
    missing = [r["ticket_id"] for r in rows if not distilled.get(r["ticket_id"])]
    print(f"tickets: {len(rows)}   chunks: {len(chunks)}   chunks with intents: {n_with_intents}   missing distilled queries: {missing or 'none'}")

    queries = {r["ticket_id"]: " ".join(distilled.get(r["ticket_id"]) or [r["subject"]]) for r in rows}

    # Per-ticket mapped intent for the ON arm (gold old-taxonomy intent -> new taxonomy).
    ticket_intent = {r["ticket_id"]: OLD_TO_NEW[r["old_intent"]] for r in rows}

    # Diagnostic: for how many tickets does the mapped intent even appear on ANY gold
    # chunk? (Upper bound on how much the prior *could* help — if the gold chunk itself
    # was never intent-labeled with the ticket's intent, the boost cannot promote it.)
    gold_intent_hits = sum(
        1 for r in rows
        if any(ticket_intent[r["ticket_id"]] in chunk_intents.get(pid, set()) for pid in r["gold"])
    )
    print(f"tickets where the mapped intent tags >=1 gold chunk: {gold_intent_hits}/{len(rows)}")

    # --- dense side: production leaf-title embed, identical across arms/prior ---
    embedder = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
    doc_vecs = embedder.encode([f"{leaf(c['title'])} {c['content']}" for c in chunks],
                               normalize_embeddings=True)
    qvecs = embedder.encode([queries[r["ticket_id"]] for r in rows], normalize_embeddings=True)
    dense_rank = {r["ticket_id"]: [ids[i] for i in np.argsort(-(doc_vecs @ qvecs[qi]))]
                  for qi, r in enumerate(rows)}

    # --- BM25 side: single production corpus (post-E007 tag-drop) ---
    bm25 = build_retriever_from_kb(KB_PATH)
    bm25_rank = await bm25_rankings(bm25, rows, queries, len(ids))

    # --- score all (prior, backend) combinations ---
    results = {}
    total_boosted = 0
    for prior in ("prior_off", "prior_on"):
        for backend in ("dense", "fusion"):
            srows, rr, gold_ranks, misses = [], [], [], 0
            for r in rows:
                tid = r["ticket_id"]
                gold = set(r["gold"])
                if backend == "dense":
                    ranking = dense_rank[tid]  # never boosted (fusion-only prior)
                else:
                    intent = ticket_intent[tid] if prior == "prior_on" else ""
                    ranking, n_boosted = fuse(bm25_rank[tid], dense_rank[tid],
                                               prior_intent=intent, chunk_intents=chunk_intents)
                    if prior == "prior_on":
                        total_boosted += n_boosted
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
            results[(prior, backend)] = agg

    # ---- print ----
    cols = ["hit@1", "hit@3", "hit@5", "recall@3", "recall@5", "ndcg@3", "mrr", "mean_gold_rank"]
    print(f"\n{'prior':10s} {'backend':7s} " + " ".join(f"{c:>8s}" for c in cols))
    for backend in ("dense", "fusion"):
        for prior in ("prior_off", "prior_on"):
            a = results[(prior, backend)]
            print(f"{prior:10s} {backend:7s} " + " ".join(f"{str(a[c]):>8s}" for c in cols))

    print("\nΔ prior_on − prior_off (same backend):")
    for backend in ("dense", "fusion"):
        off, on = results[("prior_off", backend)], results[("prior_on", backend)]
        d = {c: round(on[c] - off[c], 3) for c in ["hit@1", "hit@3", "hit@5", "recall@3", "ndcg@3", "mrr"]}
        print(f"  {backend:7s} " + "  ".join(f"{k} {v:+.3f}" for k, v in d.items()))

    print(f"\ntotal (ticket, chunk) boosts applied across all 37 fusion|prior_on scorings: {total_boosted}")

    print("\nSanity — 'prior_off | fusion' vs E007 'tags_off | fusion' / E008 'minilm | fusion' "
          "(expect hit@1 .730, hit@3 .892, hit@5 .919, ndcg@3 .645, mrr .814, rank 2.0):")
    a = results[("prior_off", "fusion")]
    print(f"  got: hit@1 {a['hit@1']}, hit@3 {a['hit@3']}, hit@5 {a['hit@5']}, "
          f"ndcg@3 {a['ndcg@3']}, mrr {a['mrr']}, mean_gold_rank {a['mean_gold_rank']}")

    out = REPO_ROOT / "backend" / "reports" / "e010_intent_prior.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"n_tickets": len(rows), "n_chunks": len(chunks), "n_chunks_with_intents": n_with_intents,
         "gold_intent_hits": gold_intent_hits, "intent_prior_boost": INTENT_PRIOR_BOOST,
         "results": {f"{p}|{b}": v for (p, b), v in results.items()}}, indent=2))
    print(f"\nreport -> {out}  (n={len(rows)})")


if __name__ == "__main__":
    asyncio.run(main())
