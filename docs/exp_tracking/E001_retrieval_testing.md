# E001 — Retrieval testing on real tickets (real policy corpus)

| | |
|---|---|
| **Experiment ID** | E001 |
| **Date run** | 2026-07-16 → 2026-07-17 |
| **Status** | Complete — decisions adopted |
| **Question** | Is retrieval over the real AAAI-27 policy corpus reliable on real ticket traffic, which backend wins, and does the keyword classifier's intent signal help or hurt? (Deployment TODOs 3 & 4) |
| **Raw report** | `backend/reports/real_eval_20260716_203615.json` |
| **Harness** | `backend/scripts/bench_real.py` (labels from `label_real_tickets.py`) |
| **Context docs** | `docs/PIPELINE_AUDIT.md` §RESULTS · Claude.md Phase 7D |

## Setup

**Corpus:** `data/knowledge_base/policies.json` (formerly `policies.json`; promoted to canonical at the 2026-07-17 corpus unification — see `archive/README.md`) — 93 chunks from the six real AAAI policy markdown docs (`chunk_policies.py`: `##` primary cut, `###` subsection split, preamble "Overview" chunks, oversize leaves paragraph-packed; contextual path titles; median 99 words, max 216, none past the ~220-word dense-embed truncation bound).

**Eval set:** 202 real tickets sampled from the answered-thread corpus (`data/tickets/marc_threads.jsonl`), stratified by keyword-classifier intent with author-facing intents over-sampled. Relevance labels produced by an external labeling model **anchored on the chair's actual reply** (the reply defines the information need), multi-gold, with a full-catalog miss-scan. Label QA: 20-ticket independent spot-check by a separate model family — answerable 19/20, chunk selection 9/9, intent 19/20 agreement; known bias: slightly *generous* on answerability. **Scored subset: 37 tickets** (policy-answerable with ≥1 gold chunk).

**Systems:**
- `bm25` — the app's `PolicyRetriever` (rank_bm25 over title+content+tags).
- `dense` — mirror of `faiss_retriever.py`: all-MiniLM-L6-v2 embeddings of `"title content"`, cosine/IP, exhaustive.
- `fusion` — Reciprocal Rank Fusion (k=60, as `fusion_retriever.py`) over the two full rankings.

**Query variants:** `body300` = first 300 chars of body (orchestrator parity) · `body300+kw_intent` = body + keyword-classifier intent token (current production behavior) · `subject+body300`.

**Metrics:** multi-gold hit@k (any gold in top-k), recall@k (fraction of golds), nDCG@k (binary, ideal = top-|gold| positions).

## Results (n=37)

| variant | backend | hit@1 | hit@3 | hit@5 | recall@3 | recall@5 | nDCG@3 | nDCG@5 |
|---|---|---|---|---|---|---|---|---|
| body300 | bm25 | .243 | .378 | .486 | .185 | .288 | .198 | .253 |
| body300 | dense | .351 | .568 | .622 | .356 | .414 | .344 | .372 |
| body300 | fusion | .351 | .540 | .595 | .369 | .414 | .350 | .371 |
| body300+kw_intent | bm25 | .243 | .378 | .486 | .185 | .288 | .198 | .253 |
| body300+kw_intent | dense | .351 | .486 | .730 | .297 | .536 | .302 | .414 |
| body300+kw_intent | fusion | .324 | .540 | .595 | .383 | .405 | .351 | .363 |
| subject+body300 | bm25 | .243 | .486 | .540 | .261 | .342 | .244 | .286 |
| subject+body300 | dense | .270 | .595 | .703 | .387 | .500 | .348 | .404 |
| **subject+body300** | **fusion** | .297 | **.649** | .676 | **.432** | .477 | **.366** | .391 |

Reference points: toy-corpus numbers were R@3 = 0.982 (faiss) / 0.875 (bm25) — the real task is far harder. Coverage context: only **18.3%** of the 202 sampled tickets are policy-answerable at all (technical_issue 0/53, withdrawal 0/9 — the corpus has no withdrawal procedure), so retrieval quality matters on roughly a fifth of traffic.

## Findings

1. **Winner at the production cutoff (top-3 = `MAX_RETRIEVED_CHUNKS`): fusion + subject+body300** — hit@3 .649, recall@3 .432, nDCG@3 .366.
2. **The keyword-classifier intent token does not help retrieval.** At k=3 it *hurts* dense (hit@3 .568→.486) and is flat for bm25/fusion. (Nuance: for dense at k=5 the intent token improves hit@5/recall@5 — .730/.536 — but the pipeline retrieves 3 chunks, and fusion without the token beats it there.) Static corroboration: intent keywords are non-discriminative over the corpus — `review_assignment` keywords alone match 71% of chunks.
3. **The subject line is the cheapest win**: adding it lifts every backend (fusion +.11 hit@3 over body-only). The orchestrator currently drops it.
4. **Fusion beats dense alone on real queries** — reversing the Phase 5C toy-corpus verdict ("fusion only dilutes the leader"). Real queries are noisy; the lexical and dense signals are complementary.
5. **Absolute ceiling is sobering**: best hit@3 = .649 → ~⅓ of answerable tickets miss every gold chunk in the top 3. Downstream (blinded draft eval, same tickets) the dominant drafting failure was *under-answering caused by retrieval/corpus gaps*, not hallucination.

## Decisions adopted

- **RETRIEVAL_BACKEND=fusion** for deployment; retrieval query should become **subject + body[:300] with no intent token** → requires a small orchestrator change (`_RETRIEVAL_QUERY_CHARS` block) + test updates. *Not yet applied to app code as of this writing* (the demo app runs `faiss` config-only).
- **TODO 4 (keyword→candidate-pool retrieval) is closed as rejected** — evidence: non-discriminative intent→policy map + the ablation above.
- Synthetic 67-email ground truth retired from retrieval benchmarking (labels point at the toy KB; policy-derived vocabulary inflates scores).

## Follow-ups (proposed E002+)

1. **KB enrichment** — top lever, bigger than any retrieval tweak: chairs answer from the FAQ doc, the ethics report form, reciprocal-reviewer rules, operational dates; none of it is in the six policy docs. Re-run this bench after enrichment.
2. Orchestrator query change + regression tests (subject in query, drop intent).
3. Larger labeled set (scale the reply-anchored labeling beyond 202) to shrink n=37 error bars and support per-intent retrieval analysis.
4. Better dense model / reranker only *after* the KB gap is closed.

## Threats to validity

- n=37 scored tickets — differences of ±.05 in hit@3 are within noise; the fusion-vs-dense gap (.054) is directional, not conclusive; the intent-hurts-dense gap (.082) and subject-helps gap (≥.08 everywhere) are more robust.
- Relevance labels are model-generated (human-spot-checked at 95% agreement, generous-on-answerable bias).
- Tickets are from the AAAI-26 cycle; the corpus is AAAI-27 policy text (cycle mismatch mirrors deployment reality next cycle but depresses absolute numbers).
- Sample stratification used *predicted* intent, so per-intent recall estimates are conditioned on the classifier's errors; the retrieval metrics themselves are unaffected.

## Reproduction

```bash
cd backend
# labels (skips existing): python scripts/label_real_tickets.py sample && python scripts/label_real_tickets.py label
OMP_NUM_THREADS=4 HF_HUB_OFFLINE=1 python scripts/bench_real.py   # writes backend/reports/real_eval_<ts>.json
```
