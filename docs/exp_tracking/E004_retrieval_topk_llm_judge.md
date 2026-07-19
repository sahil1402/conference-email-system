# E004 — Retrieval top-k (3 vs 5) under LLM-as-judge on the 20-email test set

**Date:** 2026-07-19 · **Trigger:** we bumped `MAX_RETRIEVED_CHUNKS` 3→5 and
wanted to know whether retrieving more policy chunks improves draft quality
against the chair's actual replies.

## Setup

- **Harness:** `backend/scripts/judge_testset.py`. Two stages in one run:
  (1) regenerate all 20 drafts through the **current** pipeline
  (`QUERY_STRATEGY=distill`, `RETRIEVAL_BACKEND=fusion`) against a throwaway
  SQLite DB seeded with the 93 real policies — never stale; (2) score each fresh
  draft vs the chair's actual reply (`chair_reply`) with an LLM judge, 1–5 per
  dimension.
- **Model:** `gpt-5.5` (OpenAI-compatible endpoint) for **both** drafting and
  judging, in both runs — so the only variable is retrieval top-k.
- **Data:** `data/eval_real/testset_20.jsonl` (20 real AAAI tickets + Marc's
  replies; gitignored PII). Reports/scores in `data/eval_real/` (gitignored):
  `testset_20_judge_report.md` (latest = top-5), `…_report_top3.md` +
  `…_judge_top3.jsonl` (preserved baseline).
- The judge treats a correct, well-placed `[CHAIR: …]` deferral as partial
  credit (not a factual error); a deferral of something the chair answered
  directly counts as *less complete*.

## Result — top-3 (baseline) vs top-5

| dimension | top-3 | top-5 | Δ |
|---|---|---|---|
| factual_correctness | 3.45 | 3.65 | **+0.20** |
| completeness | 2.35 | 2.35 | 0.00 |
| helpfulness | 2.40 | 2.45 | +0.05 |
| tone | 4.25 | 3.95 | **−0.30** |
| overall | 2.60 | 2.50 | −0.10 |
| drafts with ≥1 `[CHAIR]` placeholder | 11/20 | 11/20 | 0 |

**Per-ticket overall (n=20):** 14 unchanged · 2 improved (`15749`, `16711`,
each +1) · 4 regressed (`15895`, `12818`, `15765`, `15922`, each −1). Net −2
points → −0.10 mean.

## Reading it

- **No clear win from top-5.** The small factual gain (+0.20 — extra chunks let
  a few drafts ground more claims: e.g. `16217` 2→4, `16381` 3→5, `16179` 3→4)
  is offset by a tone dip (−0.30) and a flat-to-slightly-lower overall. On a few
  tickets the extra context added noise rather than signal (`16163` factual
  4→3, `12818` 2→1).
- **Completeness — the weakest dimension (2.35) — did not move at all.** This is
  the key point: the quality bottleneck is **not** retrieval breadth. Drafts
  lose completeness by deferring (`[CHAIR: …]`) things the chair answered
  directly, and/or not covering everything the chair did — retrieving more
  chunks doesn't change that behavior (placeholder count held at 11/20).
- **Within noise.** The `gpt-5.5` drafter is non-deterministic (reasoning model,
  default sampling), so a ±0.1 overall across 20 tickets is consistent with
  run-to-run variance from re-drafting alone — we have one run per setting, so
  the −0.10 overall cannot be cleanly attributed to top-k vs sampling.

## Conclusion / recommendation

Top-5 ≈ top-3 on this eval; it does not improve overall quality and slightly
hurts tone. Retrieval **count** is not the lever for the real weakness
(completeness / over-deferral — see E003 for a retrieval *precision* case, and
the FAQ over-answering finding). Keeping `MAX_RETRIEVED_CHUNKS=5` is harmless
(marginally better factual grounding, negligible cost at these volumes) but not
a win; reverting to 3 would lose nothing measurable. Next lever for completeness
is the drafter/deferral policy, not top-k. To make top-k A/Bs conclusive in
future, hold the drafter's sampling fixed (or average several runs) so draft
noise doesn't dominate a ~0.1 judge delta.
