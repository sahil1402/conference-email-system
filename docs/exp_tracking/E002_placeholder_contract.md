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

## Notes / follow-ups

- Backfilled Phase 7D drafts in the app DB predate the contract and keep their
  old body text (eval artifacts; not re-drafted by design).
- The leak detector's `policy context|information|text` pattern is
  intentionally broad — it only flags (warning note), never blocks or edits.
- Tests: drafter contract unit tests (`test_drafter_local.py`), approve-gate
  tests (`test_draft_diff.py`), FAQ→human_review downgrade integration test
  (`test_chair_routing_integration.py`), tracing field update
  (`test_tracing.py`).
