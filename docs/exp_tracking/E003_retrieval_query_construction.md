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

## Recommendations (not yet implemented)

1. **Query distillation before retrieval** — strongest lever in this data: a
   one-line "core ask" extraction moved gold from #32 to #1. Could ride the
   existing classifier LLM call or a cheap dedicated one; needs a full
   37-ticket ablation before adoption.
2. **`MAX_RETRIEVED_CHUNKS` 3 → 5** with body[:600] — cheap standalone win
   (hit@5 .730 vs .676); the drafter is grounded, so extra chunks are
   low-risk.
3. KB phrasing: gold chunk titles ("Paper Modification Guidelines") share
   little vocabulary with how requesters actually ask; enrichment/retitling
   helps lexical recall (ties into the E002/audit KB-enrichment track).
