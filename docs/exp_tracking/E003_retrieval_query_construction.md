# E003 — Retrieval query construction (case study: ticket 16396)

**Date:** 2026-07-17 · **Trigger:** chair review of the curated 20-email test
set — the draft for a co-author-addition request placeholdered a question the
chair answered definitively ("The policy is *very* clear that no authors may
be added after the submission deadline").

## Is the chair's ruling in the corpus? Yes.

- `policy_186` (*Paper Modification Guidelines*): the **list of authors** and
  author order can be changed only between the abstract deadline and the July
  28 full-paper deadline; after that, until the end of review, the submission
  is frozen.
- `policy_175` (*Changes to Titles/Authors after Submission*):
  "MODIFICATIONS TO SUBMISSIONS ARE ONLY ALLOWED UNTIL THE FULL PAPER
  DEADLINE… **No exceptions will be granted.**"

The labeler had marked exactly these two chunks as gold. The drafter behaved
correctly for the context it was given (grounded partial answer + one
placeholder, no invention) — the failure was retrieval.

## Why retrieval missed

Query = `subject + body[:300]`. This email's first 300 chars are greeting
pleasantries plus the paper title ("MemeWeaver … Sexism and Misogyny
Identification…"), whose tokens actively poison the query; the actual ask
appears after the cutoff. Retrieved top-3 were publication-ethics/authorship-
responsibility chunks (topical cousins, wrong policies).

Gold ranks for this ticket (fusion / bm25 / dense over all 93 chunks):

| query | policy_186 | policy_175 |
|---|---|---|
| subject+body[:300] (current) | #32 / #43 / #24 | #42 / #52 / #27 |
| subject+FULL body | **#2** / #5 / #4 | #21 / #49 / #9 |
| distilled ask ("add co-author change author list after submission deadline") | **#1** / #1 / #1 | **#2** / #2 / #2 |

## Is longer-query the fix? No — it doesn't generalize.

All 37 gold tickets, fusion:

| query | hit@3 | hit@5 |
|---|---|---|
| subject+body[:300] (current) | .649 | .676 |
| subject+body[:600] | .649 | **.730** |
| subject+FULL body | .622 | .730 |

Full body helps 16396 but hurts as many others (pleasantries/title noise cuts
hit@3 to .622). This ticket is one of the documented ~⅓ of answerable tickets
under the 0.65 hit@3 ceiling (E001) — a query-formulation limit, not a
backend limit.

## Distillation ablation (same day) — RESULT: adopt-worthy

`scripts/query_distill_ablation.py`: the external model turns each email
(subject + full body, 4k cap) into 1–3 compact policy-vocabulary queries.
Instruction: one line per distinct policy question — actor, action, object,
process stage; bans greetings/backstory/names/paper ids/titles/years; email
treated as data (injection guard). No corpus vocabulary given (round-two
escalation if needed — it wasn't). Distillations cached in
`data/eval_real/distilled_queries.jsonl`.

37 gold tickets, fusion backend:

| arm | hit@3 | hit@5 | recall@3 | recall@5 |
|---|---|---|---|---|
| A subject+body[:300] (current) | .649 | .676 | .441 | .477 |
| B subject+body[:600] | .649 | .730 | .410 | .518 |
| **C distilled, lines joined** | **.892** | **.892** | **.649** | **.698** |
| D distilled, per-line RRF | .838 | .838 | .595 | .626 |

- **+24 points hit@3** over the E001 ceiling; joining beats per-line RRF
  (splitting dilutes signal when one line is off-target).
- Ticket 16396 under C: gold at **#1 and #3** — the drafter would have had
  the chair's exact ruling in context.
- The 4 remaining misses (16720, 19702, 16701, 17871) all have *correct*
  distilled queries; the gold chunks' vocabulary/coverage is the limiter →
  the KB-enrichment tail, not query formulation.

## Recommendations

1. **Adopt distilled-joined queries in the orchestrator** (supersedes E001's
   pending "subject+body, no intent token" change): distill → fusion
   retrieval, falling back to subject+body[:600] on distillation failure.
   Cost: one extra model call per email; it could double as the intent
   classifier (the keyword classifier is the known weak stage) — design
   choice for adoption time.
   - ✅ **IMPLEMENTED same day**: `app/pipeline/distiller.py` (one call →
     INTENT/CONFIDENCE/QUERY lines; never raises), orchestrator gated by
     `QUERY_STRATEGY=distill` (default "prefix" = legacy bit-for-bit), intent
     from the same call rides `ClassificationResult(method="llm_distiller")`,
     keyword classifier + subject+body[:600] on any failure.
     `RETRIEVAL_BACKEND=fusion` + `QUERY_STRATEGY=distill` set in deploy .env.
     Tests: `tests/test_distiller.py` (parse/failure/wiring/fallback/legacy);
     conftest now forces hermetic model settings for the whole suite.
2. `MAX_RETRIEVED_CHUNKS` 3 → 5 — still cheap and complementary (recall@5
   .698 under C).
3. KB phrasing/enrichment for the 4-ticket tail (reviewer deadline
   extension, late submission, PC-member withdrawal, reviewer reassignment).
