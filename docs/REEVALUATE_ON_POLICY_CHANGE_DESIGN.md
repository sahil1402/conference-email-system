# Re-evaluate Open Tickets on Policy Change — Design

Status: **DRAFT for review** · Date: 2026-07-19 · Builds on: `main` (layered KB + chair governance)

## 1. Problem

When a chair changes the KB (adds an internal policy, retires, reactivates, or
reverts one), open tickets whose retrieval now shifts should be **re-drafted** so
previously-deferred `[CHAIR: …]` answers can resolve automatically. Tickets whose
retrieval is unaffected must be left untouched — no wasted drafter calls. This
closes the loop the chair described: *make your edits → re-evaluate → watch the
open deferrals they resolve.*

## 2. Decisions (locked)

| # | Decision | Choice |
|---|----------|--------|
| A | What a re-draft does | **Overwrite the stored auto-draft in place**; **skip** any ticket whose draft the chair has manually edited (never clobber). |
| B | In-progress visibility | Each affected ticket carries a transient **`redrafting`** state, surfaced in the queue/detail ("re-drafting…"), set when queued and cleared when the new draft lands (live via SSE). |
| C | Trigger | A **"Re-evaluate open tickets" button** on the KB page — the chair edits freely, then triggers **one** sweep. Individual policy edits do NOT auto-re-draft (avoids re-drafting a ticket once per edit). |
| D | Which changes count | **All** of them — add / retire / reactivate / **revert** (revert is a retire/reactivate under the hood, so it's caught for free). |
| — | "Open" ticket | `status == DRAFT_GENERATED`, not approved/sent. |
| — | Gate cost | **Free** — retrieval queries persisted at ingest let the sweep re-run *fusion only* (no model call) to decide affected-ness; only affected tickets cost a drafter call. |

## 3. The gate — "retrieval changed vs draft time"

Each policy edit already calls `rebuild_index()`, so at button-click time the
index reflects every edit in the session. A ticket is **affected** iff:

```
fresh_topk_ids  ≠  stored_topk_ids
```

- **`stored_topk_ids`** — the top-`MAX_RETRIEVED_CHUNKS` policy ids that grounded
  the ticket's *current* draft (captured at draft time).
- **`fresh_topk_ids`** — re-run the fusion retriever with the ticket's **stored
  retrieval queries** against the current index.

If the grounding set changed, the draft's basis changed → re-draft. This one
comparison uniformly captures every change kind:
- **add / reactivate** → a now-active policy enters the top-k → set changes.
- **retire / revert-an-add** → the removed policy drops out, another takes its
  slot → set changes.
- **revert-a-retire** → the reactivated policy re-enters → set changes.

Re-running the **distiller** per ticket would be a model call each; persisting the
queries at ingest makes the gate pure index lookups (no model calls).

## 4. Mechanism

1. **Persist retrieval context at ingest** (`orchestrator.process_email`): store
   the effective retrieval `queries` (distilled queries in distill mode; the
   prefix query otherwise) and the drafting `retrieved_ids` (top-k) on the email
   (a JSON field). Small, additive.
2. **Button → endpoint** — the KB page's "Re-evaluate open tickets" button calls
   `POST /api/v1/policies/reevaluate`, which schedules **one** background sweep
   (FastAPI `BackgroundTasks`) and returns immediately with `{"open": <n>}` so the
   UI can say a sweep started. (Individual create/retire/reactivate endpoints are
   unchanged except that they keep calling `rebuild_index()`.)
3. **`reevaluate_open_tickets()`** (background, its own DB session):
   - Load open tickets (`DRAFT_GENERATED`, not approved).
   - For each, re-run fusion with the stored queries → `fresh_topk` chunks.
     Affected iff `fresh_topk_ids ≠ stored_topk_ids` (§3).
   - **Affected & not chair-edited:** set `redrafting=true` (emit SSE); re-draft
     with `fresh_topk` (drafter → new draft/placeholders/citations); re-route
     (lane may flip as placeholders resolve); persist the new draft + updated
     `retrieved_ids`; clear `redrafting` (emit SSE); audit `ticket_redrafted`
     (before/after placeholder counts).
   - **Affected & chair-edited:** skip; audit `ticket_redraft_skipped_edited` so
     the chair sees it *would* change but their edit was preserved.
   - Unaffected: nothing.
4. **Concurrency:** a ticket already `redrafting` is not re-queued by a second
   button click until its current pass finishes (idempotent — a re-drafted
   ticket's `retrieved_ids` now matches, so a repeat click is a no-op for it).

## 5. Schema

Add to `emails` (one Alembic migration):
- `redrafting` — `Boolean`, default `False` (the transient state for B).
- `retrieval_context` — `JSON`, nullable: `{"queries": [...], "retrieved_ids": [...]}`
  captured at ingest for the free gate.

## 6. Components

| unit | change |
|---|---|
| `orchestrator.process_email` | persist `retrieval_context` (queries + top-k retrieved_ids) |
| `app/pipeline/reevaluation.py` (new) | `reevaluate_open_tickets()` — gate + re-draft + audit + SSE |
| `app/repositories/email_repository.py` | list open tickets; set/clear `redrafting`; update draft/routing/retrieved_ids |
| `app/api/v1/policies.py` | `POST /policies/reevaluate` → schedule the background sweep, return open count |
| email serialization (`emails.py`) + `Email` type | expose `redrafting` |
| frontend KB page | "Re-evaluate open tickets" button (+ inline "sweeping N tickets…") |
| frontend queue row + detail | "re-drafting…" badge/spinner while `redrafting` |
| chair-edited detection | reuse the existing chair-edit-diff signal (Phase 5F: original vs edited draft) to decide skip |

## 7. Testing

- gate: a ticket whose stored queries now surface a new policy → `fresh ≠ stored`
  → affected; an unrelated ticket → `fresh == stored` → not affected; a retire that
  drops a ticket's cited policy → affected; a re-drafted ticket → repeat sweep is a
  no-op (ids now match).
- re-eval: affected auto-draft ticket re-drafted + audited + `retrieved_ids`
  updated; chair-edited ticket skipped + audited; `redrafting` set then cleared.
- endpoint: `POST /policies/reevaluate` returns the open count and schedules the
  sweep.
- hermetic (in-memory DB, fallback drafter — assert the draft is *regenerated* and
  audit rows written, without a real model call).

## 8. Out of scope (YAGNI)

Preview/accept flow (we overwrite in place per decision A) · auto-trigger on every
edit (button per decision C) · re-evaluation on a public re-scrape (only the button
triggers it) · re-classifying intent (unchanged; re-eval re-retrieves + re-drafts +
re-routes only) · retrying failed re-drafts (logged, ticket left as-is with
`redrafting` cleared).
