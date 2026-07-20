# E010 — Intent-prior ablation: does B5's fusion boost help retrieval?

**Date:** 2026-07-20 · **Trigger:** B5 added a soft additive intent-prior boost
inside `FusionRetriever.retrieve()` — a chunk whose `intents` (B3-labeled) include
the email's classified intent gets `INTENT_PRIOR_BOOST` added to its fused RRF
score. It ships wired in unconditionally (`orchestrator.py` always passes
`prior_intent=classification.intent`, no feature flag) on the default
`RETRIEVAL_BACKEND=fusion` config. Before trusting it, measure it on the same
37-gold real-ticket set the retrieval line (E001/E003/E005/E007/E008) has used
throughout.

## Setup

- **Harness:** `backend/scripts/e010_intent_prior_ablation.py` (retrieval-only, no
  model calls at run time). Reuses `bench_real.score_ranking` verbatim and imports
  `INTENT_PRIOR_BOOST` straight from `fusion_retriever.py` (never a re-typed
  constant), so results can't silently drift from production.
- **Data (gitignored):** the same 37 real-gold answerable tickets
  (`data/eval_real/labels.jsonl` ∩ `sample.jsonl`) + the E003 distilled query cache.
- **Query config:** distilled queries joined into one string (E005/E007/E008
  parity), fusion = RRF(k=60) over BM25 + dense; dense = production leaf-title
  embed (E005).
- **Chunk `intents`:** read straight from `data/knowledge_base/policies.json`
  (B3-labeled; 49/93 chunks currently non-empty).
- **Arms:** `prior_off` (current pre-B5 baseline, no boost) vs `prior_on` (B5's
  boost applied post-fusion, per-ticket).
- **Per-ticket intent for the ON arm — documented choice.** No cached distiller
  classification in the *new* 14-intent taxonomy exists for this eval set (checked
  every file under `data/eval_real/`: only the *old* 11-intent gold `intent` field
  and the *old* keyword `kw_intent` are cached). Re-running the distiller live would
  add a model call to what should be a retrieval-only harness, so instead each
  ticket's gold `intent` (old taxonomy) is mapped through the Task-B6-brief's
  old→new table onto the new taxonomy (`OLD_TO_NEW` in the script). This was
  hand-checked against all 37 tickets' rationale text — the default mapping fits
  every ticket actually present (e.g. no `ethics_concern` ticket in this set needed
  the appeal-flavoured `review_decision_appeal` alternative; all 7 are
  multiple-submission / anonymity / misconduct *violation reports*, matching
  `anonymity_violation`). **Caveat (real, not just formal):** this is a clean
  upper-bound proxy, not production behaviour. Production intent comes from the
  live classifier/distiller, which mis-fires sometimes (E007/bench_real:
  intent-accuracy ~58% on real traffic) — a wrong or noisy intent doesn't just fail
  to help, it can boost the *wrong* chunk. The numbers below are thus a best case
  for the prior, not a worst case.
- **Backends: dense + fusion.** BM25-alone and dense-alone are **not** boosted in
  either arm — the boost is fusion-only by construction
  (`faiss_retriever.py`/`retriever.py`: *"the soft intent prior is a fusion-only
  score boost (B5)... so B6 can ablate the boost cleanly"*). So **dense is
  identical between `prior_off`/`prior_on` by design** — confirmed below, not a
  harness bug. **Fusion is the only arm this ablation actually tests.**
- **Sanity check:** `prior_off | fusion` reproduces E007's `tags_off | fusion` /
  E008's `minilm | fusion` row exactly (hit@1 .730, hit@3 .892, hit@5 .919, ndcg@3
  .645, mrr .814, mean gold rank 2.0) — the harness matches; the delta is
  trustworthy.

## Results (n=37)

| prior | backend | hit@1 | hit@3 | hit@5 | recall@3 | recall@5 | ndcg@3 | MRR | mean gold rank |
|---|---|---|---|---|---|---|---|---|---|
| prior_off | dense | .649 | .838 | .919 | .581 | .698 | .582 | .756 | 2.5 |
| prior_on | dense | .649 | .838 | .919 | .581 | .698 | .582 | .756 | 2.5 |
| **prior_off** | **fusion** | **.730** | **.892** | **.919** | **.640** | **.712** | **.645** | **.814** | **2.0** |
| prior_on | fusion | .243 | .541 | .730 | .423 | .563 | .343 | .449 | 4.1 |

**Δ prior_on − prior_off:**
- **dense:** every metric **+0.000** — confirms the "fusion-only, dense untouched by
  design" claim exactly; this is expected, not a null result to explain away.
- **fusion (the real test):** hit@1 **−.487**, hit@3 **−.351**, hit@5 **−.189**,
  recall@3 **−.217**, ndcg@3 **−.302**, MRR **−.365**, mean gold rank 2.0 → 4.1.

This is not a small nudge — it is a severe regression on every fusion metric.

## Why it hurts: the boost magnitude vs. intent-coverage granularity

The harness instruments every (ticket, boosted-chunk) event. Across the 37 tickets,
**226 chunk-boosts fire** (mean ~6.1 chunks boosted per ticket) — because several of
B3's intent labels are broad or sparse in a way that makes the boost apply to many
chunks at once, or to one chunk across many unrelated tickets:

| new-taxonomy intent (as mapped) | tickets mapped to it | chunks tagged with it |
|---|---|---|
| `submission_format_policy` | 7 | 16 |
| `anonymity_violation` | 7 | 5 |
| `cms_support` (fallback) | 6 | 3 |
| `author_list_change` | 4 | 5 |
| `reviewer_assignment` | 12 | **1** |
| `submission_requirements` | 1 | 29 |

Two failure modes show up, both from the same root cause — the boost
(`INTENT_PRIOR_BOOST = 1/(k+1) ≈ 0.0164`) is large relative to the fused-score gaps
between adjacent documents near the top of a 93-chunk corpus:

1. **A single generic chunk absorbs an intent tag and gets boosted across many
   unrelated tickets.** `reviewer_assignment` is tagged on exactly **one** chunk
   (`policy_160`, "AAAI Publication Ethics and Malpractice Statement — Reviewer
   Responsibilities" — itself an arguable B3 mislabel; it's about misconduct
   reporting, not assignment mechanics). 12 of the 37 tickets map to
   `reviewer_assignment`. For 11 of those 12, `policy_160` is **not** the gold
   chunk, yet the boost still promotes it to rank 1 for most of them — e.g. ticket
   18464 had the correct gold chunk at rank 1 *before* the boost; after, `policy_160`
   jumps ahead of it (rank 1 → 2). Only 1 of the 12 (`17871`) actually has
   `policy_160` as gold, and that one ticket improves (rank 2 → 1).
2. **A broad intent tag boosts many chunks at once, flooding the top of the
   ranking and letting `(policy_id asc)` tie-breaking dominate over real
   relevance.** `submission_format_policy` tags 16/93 chunks; for the 7 tickets
   mapped to it, e.g. ticket 15765 had the correct chunk (`policy_186`) at rank 1
   before the boost; after, an unrelated but same-intent-tagged `policy_102` wins
   the tie-break and the correct chunk falls to rank 13.
3. **Per-ticket breakdown:** of the 37 tickets, only **14/37** even have the
   mapped intent tagged on their *own* gold chunk (the ceiling on how many tickets
   the prior could possibly help). Of all 37: **24 got worse, 2 got better, 11
   unchanged.** Even restricted to the 14 "could help" tickets, most still got
   worse — the boost frequently promotes a *different*, same-intent-tagged chunk
   ahead of the correct one rather than the correct one itself.

**Verdict on magnitude: yes, `INTENT_PRIOR_BOOST` is too strong for the current
intent-coverage density.** Sized as "a full single-ranker rank-1 RRF vote" (see the
corrected comment in `fusion_retriever.py`), it is large enough to vault a
same-intent chunk from deep in the ranking to the top regardless of how relevant it
actually is — it behaves less like a tie-breaking nudge and more like a coarse
category filter, exactly what Global Constraint #4 ("soft additive boost, never a
hard filter") was meant to avoid in effect even though it's a filter in name only.
A gentler value — e.g. a small fraction of one rank-step near the top (rough order
of magnitude: 5–10x smaller, ~0.0015–0.0033) rather than a full rank-1 vote — would
let it break near-ties without being able to overturn an already-correct top
result. The other lever is orthogonal but just as important: **intent-coverage
density.** A single chunk tagged for an intent that maps to 12 different tickets
(as with `reviewer_assignment`) guarantees the boost is mostly wrong, no matter how
it's sized, because one chunk cannot be the right answer for 12 different
questions. Both would need to move together — this ablation can't separate which
lever matters more without a follow-up magnitude sweep (not run here; out of scope
per the brief, which says not to change the boost value).

## The E009 caveat, restated

**This 37-gold set is the over-sampled author-submission slice** (E009): the
labeling pass deliberately over-sampled author-facing, policy-answerable intents
because the corpus otherwise skews ~68% reviewer-ops traffic with near-zero KB
coverage. So this result — including the regression above — is measured **only on
that friendly slice**. The prior's value (or harm) on the reviewing-ops majority of
the real inbox is **completely unmeasured** until the eval expands to cover it.
Two directions this cuts:
- The regression mechanism found here (sparse/broad intent tags flooding a small
  candidate pool) is structural, not slice-specific — it would very plausibly
  reproduce or worsen on reviewing-ops tickets, where KB coverage is even thinner
  and intent-to-chunk tags are likely sparser still.
- But it is not *proven* on that majority traffic. No claim here should be read as
  "the prior is bad for reviewing-ops" — only "the prior is bad on the one slice we
  can currently measure, for a mechanism that generalizes in principle."

## Decision

**Do not adopt `prior_intent` boosting as currently sized on this eval.** The
measured effect on the only arm it touches (fusion) is a severe regression (hit@1
−.487, MRR −.365), not the gentle nudge it was designed to be. Because
`prior_intent` is wired in **unconditionally** (no feature flag — `orchestrator.py`
always passes `classification.intent`) on the **default** `RETRIEVAL_BACKEND=fusion`
config, this is a live-path finding, not a hypothetical: any current fusion-backend
deployment is exposed to this regression today, gated only by how often
`classification.intent` and a chunk's `intents` happen to line up in production
traffic (unmeasured — this eval used a gold-label proxy, not the live classifier).

This task (B6) is scoped to *measure*, not to fix — per the brief, the boost value
and scoring logic are explicitly not to be changed here. Recommended follow-up
(not done in this task): (1) a magnitude sweep (smaller `INTENT_PRIOR_BOOST`
values) to find a size that helps or is at least neutral; (2) densifying/auditing
B3's intent→chunk coverage so no single intent collapses onto one chunk; (3) until
either lands, consider gating `prior_intent` behind a flag (defaulting off) rather
than leaving it unconditional, so a fusion deployment doesn't inherit this
regression silently.

## Threats to validity

- n=37; single-threshold metrics (hit@k) are ±.05 noise (E001) — but this delta
  (−.487 hit@1, −.365 MRR) is far outside that band and consistent across every
  metric, so it is not noise.
- The ON-arm intent is a gold-label proxy (see Setup), not the live classifier —
  real production noise could make this better *or* worse; untested here.
- Only the fusion arm is exercised meaningfully; dense is unaffected by
  construction (confirmed, not assumed).
- Same E009 selection-bias caveat as always: author-submission slice only: see
  above.

## Reproduction

```bash
cd backend
export PATH=/u/jpang1/miniconda3/envs/autoexp/bin:$PATH
# Run with the app STOPPED (login-node thread budget). Thread caps:
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=4 MKL_NUM_THREADS=1 \
       TOKENIZERS_PARALLELISM=false HF_HUB_OFFLINE=1
python scripts/e010_intent_prior_ablation.py   # report -> backend/reports/e010_intent_prior.json
```
