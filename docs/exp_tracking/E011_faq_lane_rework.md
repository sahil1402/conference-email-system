# E011 — FAQ-lane rework: from intent whitelist to a draft-quality gate

**Date:** 2026-07-20 · **Trigger:** Task B4 (now RETIRED) had derived FAQ
eligibility from a coverage-derived intent whitelist (`FAQ_ELIGIBLE_INTENTS`):
an intent could auto-reply only if the KB coverage map showed it had
answering chunks. That whitelist is gone. This note is a design record, not
an ablation — there is no harness/metrics table below, just the change and
why it is safe.

## What changed

FAQ-lane eligibility is no longer a property of the email's *classified
intent*. It is now a property of the generated *draft*. The router's gate
requires ALL of the following to hold, or the email escalates to
human_review with a specific reason:

- No chair placeholders in the draft (`draft.placeholders` empty).
- No notes-for-chair (`draft.notes_for_chair` empty).
- Grounded: the draft cites at least one policy chunk (`draft.citations`
  non-empty).
- Classifier confidence at or above `FAQ_CONFIDENCE_THRESHOLD` (default
  `0.65`).
- Drafter self-rated `answer_confidence` at or above
  `FAQ_ANSWER_CONFIDENCE_THRESHOLD` (default `0.85`).

All five conditions are AND-ed in `app.pipeline.router.EmailRouter.route()`;
any single failure produces a distinct human-readable escalation reason.

**Route-after-draft.** The router used to run BEFORE drafting and decide the
lane from intent + confidence alone. It now runs AFTER the drafter, taking
the draft as an explicit argument (`route(classification, retrieved_chunks,
draft)`), because the gate above needs to inspect the draft's completeness,
grounding, and self-rated confidence. Non-LLM drafters (`template`,
`fallback`) never populate `answer_confidence` — it stays `None` — so those
paths can never satisfy the gate and always escalate to human_review. This
is a deliberate fail-safe, not an oversight: a drafter that cannot self-rate
its own answer never gets to auto-reply.

**`SENSITIVE_INTENTS` emptied, not removed.** The list that used to
force-escalate specific intents (e.g. appeals) regardless of draft quality
is now `[]`. Appeals are answerable: a complete, grounded reply — even one
whose answer is "no" — is auto-eligible under the draft-quality gate like
any other intent. The list is kept as a code seam in `router.py`; a future
policy or incident can re-populate it to force specific intents to a human
under any routing strategy.

**Strategy-independent self-sufficiency floor.** The rule-based router's
gate above already enforces "no placeholders, no notes" before returning
`faq`, so the floor is redundant there. It is NOT redundant for the RL
routing strategy (`ROUTING_STRATEGY=rl`, dormant — see below): the bandit
picks a lane from intent + confidence alone, before a draft exists, and
cannot see placeholders or notes-for-chair. `app.pipeline.router.
apply_self_sufficiency_floor(routing, draft)` is a small, strategy-independent
check called right after `router.route()` returns, from both
`app.pipeline.orchestrator` and `app.pipeline.reevaluation` — the two
callers that produce a `RoutingDecision` alongside a draft. It demotes any
`faq` decision to `human_review` if the draft carries placeholders or notes,
whatever routing strategy produced that decision. This guarantees a
placeholder/notes draft never auto-answers even if a future routing
strategy forgets to check draft quality itself.

**The intent→chunk coverage map lives on, demoted.**
`backend/reports/kb_intent_coverage.json` (produced by the KB-labeling
pipeline) is no longer read by the router — it is retained purely as
analytics / a KB-gap signal (which intents currently lack answering
coverage), not a routing input.

**`chairs.areas` re-seeded.** Migration `b2c3d4e5f6a7` re-seeds the `chairs`
table's `areas` column to the current 14-intent taxonomy families, replacing
the stale pre-taxonomy area lists. This is independent of the FAQ-lane
change above — it fixes chair-routing (the separate "which chair" decision),
not FAQ eligibility — but shipped in the same taxonomy-adoption branch.

**Eval harness follow-on.** `scripts/run_eval.py`'s `routing_accuracy` is now
an end-to-end, draft-aware, provider-dependent metric rather than a
router-in-isolation one, because the router it exercises reads the draft.
Run with a non-LLM provider (the hermetic test default), every draft's
`answer_confidence` is `None`, so the gate conservatively routes everything
to human_review — `routing_accuracy` under that config reflects the
ground-truth lane mix, not routing quality. A meaningful FAQ-lane number
requires running the harness against a real drafting provider.

## Out of scope

RL routing (`ROUTING_STRATEGY=rl`) remains dormant and out of scope for this
rework. It still routes from intent + confidence alone (see
`app.pipeline.rl_router`), and is protected only by the strategy-independent
safety floor described above, not by the five-condition draft-quality gate
itself. Making the bandit draft-aware is future work.

## Tests

`backend/tests/test_router.py` covers the five-condition gate (each
condition's individual failure + the appeal-is-answerable case) and the
route-after-draft signature. `backend/tests/test_routing_safety_floor.py`
covers `apply_self_sufficiency_floor` directly (placeholder demotion, notes
demotion, no-op on a self-sufficient draft) plus its wiring into both
callers. `backend/tests/test_chair_routing_integration.py` covers the
re-seeded `chairs.areas`.
