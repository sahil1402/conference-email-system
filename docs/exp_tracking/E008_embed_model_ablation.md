# E008 — Embedding-model A/B: MiniLM vs BGE (retrieval only)

**Date:** 2026-07-19 · **Trigger:** MiniLM (`all-MiniLM-L6-v2`, 2021-era, 384-d) is
the current dense embedder (`config.FAISS_MODEL_NAME`) and a suspected weak link;
BGE is a common modern RAG upgrade. Question: does swapping to BGE improve
real-ticket retrieval?

## Setup
- **Harness:** `backend/scripts/e008_embed_model_ablation.py` (retrieval-only, no LLM
  at run time). Reuses `bench_real.score_ranking`; same 37 real-gold answerable
  tickets + E003 distilled query cache as E005/E007.
- **Arms (dense embedder only; BM25 unchanged, model-independent):** `minilm`
  (all-MiniLM-L6-v2), `bge-small` (BAAI/bge-small-en-v1.5, 384-d), `bge-base`
  (BAAI/bge-base-en-v1.5, 768-d). Docs = leaf-title + content (E005). BGE query
  instruction "Represent this sentence for searching relevant passages: " applied
  to queries; passages none.
- **Config:** distilled-joined queries, fusion = RRF(k=60) over BM25 + dense.
- **Sanity:** `minilm` reproduces E005/E007 exactly — dense .649/.838/.756/rank2.5
  and fusion .730/.892/.814/rank2.0 (matches E007 `tags_off`). Harness trusted.

## Results (n=37)

| arm | backend | hit@1 | hit@3 | hit@5 | recall@3 | recall@5 | ndcg@3 | MRR | mean rank |
|---|---|---|---|---|---|---|---|---|---|
| **minilm** | dense | **.649** | **.838** | **.919** | .581 | .698 | .582 | **.756** | 2.5 |
| bge-small | dense | .568 | .784 | .892 | .554 | .671 | .539 | .695 | 3.0 |
| bge-base | dense | .622 | .811 | .892 | **.617** | **.716** | **.597** | .730 | 2.5 |
| **minilm** | **fusion** | **.730** | .892 | .919 | **.640** | .712 | **.645** | **.814** | 2.0 |
| bge-small | fusion | .595 | .838 | .892 | .599 | .680 | .587 | .733 | 2.4 |
| bge-base | fusion | .622 | .892 | .919 | .626 | .694 | .618 | .760 | 2.0 |

**Δ bge-base − minilm, fusion:** hit@1 **−.108**, hit@3 +.000, recall@3 −.014,
ndcg@3 −.027, MRR −.054. Dense: hit@1 −.027, but recall@3 **+.036**, recall@5 +.018,
ndcg@3 +.015.

## Findings
1. **BGE does not beat MiniLM on this setup — it regresses.** In production (fusion)
   bge-base loses hit@1 −.108 and MRR −.054 (~4 tickets, beyond ±.05 noise), ties
   hit@3/5. bge-small is worse across the board.
2. **bge-base's only advantage is dense recall@3/5** (pulls more gold into the top-5)
   — but fusion erases it (fusion recall@3 .626 < minilm .640).
3. **Likely cause: the pipeline is tuned to MiniLM.** The distilled-query format
   (E003) and leaf-title representation (E005) were optimized with MiniLM in the
   loop; terse distilled policy-keyword queries favour BM25/MiniLM and don't
   exercise BGE's natural-language semantic strength. Small near-ceiling corpus (93
   chunks, hit@3 .892) leaves little room.

## Decision
**Keep `all-MiniLM-L6-v2` for retrieval.** BGE is not adopted — the ablation shows a
regression on the production (distilled-query, fusion) path, and the E008b follow-up
below shows it also loses on raw queries.

## Follow-up E008b — raw vs distilled queries (2026-07-19)
E008's open caveat was that distilled queries favour MiniLM and don't exercise BGE's
natural-language strength. `scripts/e008b_raw_query.py` gives each embedder the RAW
email (subject + body) as the query (BM25 uses the same query per mode), n=37.

| model | query | backend | hit@1 | hit@3 | recall@3 | mrr | mean rank |
|---|---|---|---|---|---|---|---|
| **minilm** | distilled | fusion | **.730** | **.892** | **.640** | **.814** | 2.0 |
| bge-base | distilled | fusion | .622 | .892 | .626 | .760 | 2.0 |
| minilm | raw | fusion | .514 | .649 | .419 | .629 | 4.0 |
| bge-base | raw | fusion | .378 | .568 | .360 | .512 | 7.0 |
| minilm | raw | dense | .459 | .757 | .568 | .620 | 3.2 |
| bge-base | raw | dense | .270 | .541 | .396 | .445 | 8.3 |

**Δ bge-base − minilm on raw:** dense hit@1 **−.189**, hit@3 **−.216**, mrr −.175;
fusion hit@1 −.136, hit@3 −.081, mrr −.117.

**Findings:** (1) The confound is **resolved — BGE loses on raw queries too**, and by
a *wider* margin than on distilled (raw dense: bge-base lands gold at mean rank 8.3 vs
MiniLM 3.2). (2) **Raw ≪ distilled for both models** (minilm fusion hit@1 .730→.514),
re-confirming E003: stripping greetings/signatures/backstory is the heavy lifter, and
feeding a noisy raw email buries the policy signal — which BGE handles *worse*, not
better. **Unambiguous winner: MiniLM + distilled queries.**

## Threats to validity / open follow-ups
- **Distilled-query confound — RESOLVED (E008b above):** BGE loses on raw queries too,
  by a wider margin. Not a masking effect.
- n=37; single-threshold metrics ±.05 (E001). The verdict rests on hit@1/MRR
  (aggregate, beyond noise), consistent across bge-small/bge-base.
- **Eval-set validity:** the 37-gold set was hand-audited (E009) — labels are sound, so
  this verdict holds, but the set is the over-sampled author-submission slice and
  overstates real-inbox retrieval.
- BGE query-instruction untuned; bge-large / e5 / hosted embedders not tested.
- Retrieval ≠ the bottleneck (E006: coverage is). A separate open question is whether
  a stronger embedder improves the **clustering/taxonomy mining** geometry even
  though it loses retrieval here.

## Reproduction
```bash
cd backend
export PATH=/u/jpang1/miniconda3/envs/autoexp/bin:$PATH
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=4 python scripts/e008_embed_model_ablation.py
# report -> backend/reports/e008_embed_model_ablation.json
```
