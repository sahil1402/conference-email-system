# E002 — Chair-placeholder reply contract

**Date:** 2026-07-17 · **Branch:** `feature/chair-placeholder-contract` · **Status:** validated

## Problem

Phase 7D eval drafts leaked chair-facing meta content into the requester-facing
reply body: "the policy does not specify…", "we cannot confirm…", "we will look
into this and follow up". Measured on the 37 policy-answerable eval tickets
(guide v2 config): **32/37 replies (86%) contained at least one leak.**

Root cause was self-inflicted: the drafter system prompt said *"If the context
does not answer the question, say so plainly"*, and style guide v2 both told
the model to *"say so plainly and defer to the workflow chairs"* (§3) and
supplied the exact promise template *"We will look into this and follow up"*
(§2.1). Both predate the structured `NOTES FOR CHAIR` channel.

## Design

Chair-facing content is split out of the email into a dedicated path, and the
reply reserves editable placeholders for the chair:

1. **Placeholder contract (primary, drafter `_SYSTEM_PROMPT`)** — the reply
   must read as written by a chair with full knowledge; where the context
   can't support a needed statement, the model inserts `[CHAIR: <what to fill
   in or decide>]` inline at that exact spot, and adds a matching NOTES FOR
   CHAIR line (what's missing, knowledge gap vs. decision, suggested
   resolution). Lives in the fixed prompt — not only the guide — so it holds
   in the `guide=none` configuration too.
2. **Style guide v2 consistency edit** — §2.1 and §3 rewritten to point at the
   placeholder convention (provenance recorded in
   `data/style_guide/manifest.json`).
3. **Deterministic enforcement (no extra model call)** —
   - `DraftResponse.placeholders` parsed via `PLACEHOLDER_RE` (drafter.py);
   - orchestrator forces `human_review` lane when placeholders exist (a
     placeholder reply is never FAQ-complete; happens before chair assignment
     so the email picks up a chair);
   - approve endpoint 409s while `[CHAIR: …]` remains in the outgoing text
     (belt-and-braces under the human gate);
   - leak detector (`_LEAK_PATTERNS`) flags residual meta phrases into
     `generation_metadata.reply_leaks` + a WARNING appended to the chair
     notes — flag, never regex-rewrite prose.
4. **UI** — "Chair Suggestions" panel renders `notes_for_chair` (warning
   styling, marked never-sent); unresolved placeholders in the live edit
   disable Approve & Send with a listing of what to fill in.

## Validation

`backend/scripts/placeholder_eval.py` re-drafted the same 37 answerable
tickets (config v2, fusion retrieval, subject+body[:300] query — identical to
the Phase 7D setup) → `data/eval_real/drafts_placeholder.jsonl`.

| Metric | OLD (7D, v2) | NEW (placeholder contract) |
|---|---|---|
| Replies with chair-meta leaks | 32/37 (86%) | **0/37 (0%)** |
| Replies with `[CHAIR: …]` placeholders | 0 | 30 (57 tokens total) |
| Drafts with `notes_for_chair` | — (not captured) | 34/37 |
| Placeholder drafts missing chair notes | — | **0** |

Qualitative spot-check (5 samples reviewed in-session): placeholders land at
the exact gap point inside natural sentences; notes state gap type and a
suggested resolution; a fully-answerable ticket produced a complete reply with
zero placeholders and a caveat-only note (cycle-year mismatch).

## Rounds 2–3: conciseness + contract/style split (same day)

User review of round 1 flagged verbosity (labeled three-part notes, 14-word
hints, per-sub-fact placeholders) and UI redundancy (same info shown three
times). Two follow-up revisions, each re-validated on the same 37 tickets:

- **Round 2 (concise prompt)**: short bracket hints, telegraphic one-line
  notes (no category labels), placeholder economy — at most one per distinct
  question the requester raised, merge related unknowns, single-placeholder
  skeleton when the context supports almost nothing, never placeholder the
  unasked.
- **Round 3 (contract/style split)**: de-duplicated the system prompt vs.
  style guide. Principle: **system prompt = contract** (grounding, output
  structure, placeholder mechanics, no action claims/promises, no real names,
  no past cycles — holds with any/no guide, enforced by code), **guide =
  voice** (Marc-corpus conventions, swappable). Guide v2 lost §2.1 + all of
  §3 (~40% shorter), gained a scope/subordination header; three invariants
  moved into the drafter prompt.

| Metric (37 tickets) | r1 | r2 | r3 |
|---|---|---|---|
| Total placeholders | 57 | 30 | 34 |
| Drafts with >2 placeholders | 7 | 0 | 1 |
| Avg hint length (words) | 14.4 | 8.0 | 9.0 |
| Avg notes length (words) | 76 | 18 | 19 |
| Chair-meta leaks | 0 | 0 | 0 |

r2→r3 deltas are run-to-run variance: the split removed duplication without
behavior change. UI simultaneously de-duplicated: placeholders are highlighted
in place inside the draft editor (backdrop-overlay `<mark>`), the under-editor
warning is one line with no hint listing, and the suggestions panel caption is
one line. Raw rounds kept as `drafts_placeholder_r1.jsonl` / `_r2.jsonl` /
`drafts_placeholder.jsonl` (r3) in `data/eval_real/`.

## Round 4: raw test inputs + greeting population (same day)

User decisions: eval/test sets must use **exact, unscrubbed ticket text**
(scrubbing had masked greetings/addresses → `<name>`/`<email>` tokens in what
the drafter saw; it now survives only in style-guide distillation), and
**requester greetings are populated** from the inquiry (sender line or
signature; role-based fallback), never a placeholder token. `[Sender name]`
sign-off stays literal until the chair account system populates it.
`_build_user_prompt` now surfaces `sender_name` when the ingest provides one
(Zendesk requester profiles will, so named greetings are the production norm).

Re-run on raw text: n=37, placeholders 33, >2-placeholder drafts 0, hint 8.3
words, notes 19 words, leaks 0 — stable vs r2/r3. Greetings: 0 placeholder
tokens, 3 by actual signature name, 34 role-based (eval rows carry no sender
profile). Raw file: `drafts_placeholder.jsonl` (r4); prior rounds archived as
`_r1/_r2/_r3`.

## Notes / follow-ups

- Backfilled Phase 7D drafts in the app DB predate the contract and keep their
  old body text (eval artifacts; not re-drafted by design).
- The leak detector's `policy context|information|text` pattern is
  intentionally broad — it only flags (warning note), never blocks or edits.
- Tests: drafter contract unit tests (`test_drafter_local.py`), approve-gate
  tests (`test_draft_diff.py`), FAQ→human_review downgrade integration test
  (`test_chair_routing_integration.py`), tracing field update
  (`test_tracing.py`).
