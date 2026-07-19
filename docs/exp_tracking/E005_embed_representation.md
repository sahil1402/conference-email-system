# E005 — Embed representation: drop the doc-prefix from the dense title

**Date:** 2026-07-19 · **Trigger:** the KB-rework audit (`docs/local/RETRIEVAL_REWORK.md`
§2b) found the dense corpus barely separable — 82% of chunks had a >0.7 near-twin,
almost all *within* the same source document — and predicted that the repeated
document-name prefix embedded into every sibling chunk was a large, cheap-to-remove
cause.

## Setup

- **Harness:** `backend/scripts/e005_embed_repr.py` (retrieval-only, no model calls
  at run time). Reuses `bench_real.score_ranking` verbatim, so metrics are directly
  comparable to E001/E003.
- **Data (gitignored):** the 37 real-gold answerable tickets
  (`data/eval_real/labels.jsonl` ∩ `sample.jsonl`) + the E003 distilled query cache
  (`data/eval_real/distilled_queries.jsonl`). Gold ids all resolve into the live
  93-chunk corpus (`policy_101`–`193`).
- **Query config:** E003's winner — **distilled queries joined into one string, no
  intent token**; fusion = RRF(k=60) over full BM25 + dense rankings.
- **Arms (dense embed string only; BM25 unchanged = full title + content + tags):**
  - `full` — `"{title} {content}"` (current production, `faiss_retriever.py:100`)
  - `leaf` — `"{leaf} {content}"`, `leaf` = title after the first ` — ` (drop doc prefix)
  - `content` — `"{content}"` (reference lower bound)
- **Metrics:** multi-gold hit@k / recall@k / nDCG@k (k=1,3,5), plus MRR and mean
  best-gold rank — the last two aggregate over all tickets rather than a threshold,
  so they are less noisy than hit@3 at n=37.
- **Sanity check:** `full` + fusion reproduces E003's distilled-joined row exactly
  (hit@3 .892, recall@3 .649, recall@5 .698) → the harness matches; leaf/content
  deltas are trustworthy.

## Results (n=37)

| arm | backend | hit@1 | hit@3 | hit@5 | recall@3 | recall@5 | nDCG@3 | MRR | mean gold rank |
|---|---|---|---|---|---|---|---|---|---|
| full | dense | .514 | .784 | .892 | .586 | .649 | .541 | .665 | 2.9 |
| **leaf** | dense | **.649** | **.838** | **.919** | .581 | **.698** | **.582** | **.756** | **2.5** |
| content | dense | .514 | .811 | .919 | .559 | .707 | .537 | .678 | 2.4 |
| full | fusion | .649 | .892 | .892 | .649 | .698 | .631 | .758 | 2.3 |
| **leaf** | **fusion** | **.703** | .892 | **.919** | .640 | .694 | **.638** | **.800** | **2.1** |
| content | fusion | .676 | .892 | .919 | .644 | .716 | .639 | .782 | 2.1 |

**Δ leaf − full:** dense hit@1 **+.135**, hit@3 +.054, MRR **+.091** · fusion hit@1
**+.054**, hit@5 +.027, MRR +.042. recall@3 is flat (−.005/−.009, within noise).

## Findings

1. **Leaf wins.** Dropping the doc prefix from the embedded string improves
   retrieval on the dense half (where the change applies): hit@1 +13.5, MRR +9.1,
   mean gold rank 2.9→2.5. The gold chunk moves toward the top.
2. **Production (fusion) improves or holds.** hit@1 +5.4, hit@5 +2.7, MRR +4.2, gold
   rank 2.3→2.1; **nothing regresses**. hit@3 is flat only because this set already
   saturates at .892 — the gain is *inside* the top-3 (MRR / rank), invisible to the
   hit@3 threshold. Smaller than the dense gain because BM25 (full title, unchanged)
   is half of fusion.
3. **Keep the leaf, don't go content-only.** Leaf beats content-only on dense hit@1
   (.649 vs .514) and nDCG@3 (.582 vs .537) — the section name is real signal.
4. **The §2b confusability diagnostic predicted a real retrieval gain** — corroborated
   on live gold, not just corpus-intrinsic geometry.

## Decision adopted

**Embed the leaf title, not the full path.** `faiss_retriever.py` now builds the
dense text via `_embed_text` / `_leaf_title` (drops the `<Doc> — ` prefix). The
stored `title` is **unchanged** — BM25 still indexes the full path and citations
still display it; the change is dense-embed-only. Tests: `test_leaf_title_drops_doc_prefix`,
`test_embed_text_uses_leaf_and_content` (`tests/test_faiss_retriever.py`).

## Threats to validity

- n=37; single-threshold metrics (hit@3) are ±.05 noise (E001). The verdict rests on
  the *consistency* across hit@1 / MRR / mean-rank (which aggregate over all tickets)
  and across both backends, all moving the same direction.
- Real tickets are AAAI-26-cycle against AAAI-27 policy text (cycle mismatch, as in
  E001) — depresses absolute numbers, not the relative comparison.
- The two eval mirrors (`bench_real.py:79`, `draft_eval.py:84`) still embed the full
  title, preserving the E001/E003 baselines; only production (`faiss_retriever.py`)
  and the live-pipeline judge (`judge_testset.py`) use the leaf. Reconcile the mirrors
  if they are used for a fresh ablation.

## Reproduction

```bash
cd backend
export PATH=/u/jpang1/miniconda3/envs/autoexp/bin:$PATH
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=4 HF_HUB_OFFLINE=1 python scripts/e005_embed_repr.py
# report -> backend/reports/e005_embed_repr.json
```
