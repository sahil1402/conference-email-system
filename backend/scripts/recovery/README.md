# `scripts/recovery/` — one-off recovery & data-fix scripts

Scripts in this folder are **operational one-offs**: incident recovery, data
repair, and diagnostic sweeps run by hand when something went wrong. They are
deliberately kept apart from `backend/scripts/` (seeding, eval harnesses,
retrieval ablations, corpus tooling), which is *regular pipeline tooling* that
gets run repeatedly as part of normal development.

## Rules for anything added here

1. **Not part of the running app.** Nothing in `app/` imports from this folder.
   These are standalone `python scripts/recovery/<name>.py` entry points; the
   FastAPI app, the pipeline modules (classifier / retriever / router / drafter),
   and the scheduler never reference them.
2. **Reuse, never re-implement.** Auth, clients and config come from the real
   modules (e.g. `app.integrations.zendesk.credential_provider`,
   `app.core.config.settings`). A recovery script must not carry its own copy of
   OAuth logic or credentials.
3. **Read-only by default.** A diagnostic/discovery script must not write to any
   external system. Write-capable repair scripts are separate files, are named
   so the intent is obvious, and are gated on a human having reviewed the
   discovery report first.
4. **Output is untracked.** Reports land in `scripts/recovery/output/`, which is
   gitignored — these reports quote real ticket content (PII) and must never be
   committed.
5. **Tested.** Even a one-off gets a test under
   `backend/tests/recovery/` with the external API mocked, because these
   scripts run against production data exactly once, under time pressure, and
   there is no staging rehearsal.

## Current contents

| Script | Mode | Purpose |
|---|---|---|
| `find_internal_notes.py` | **read-only** | Find comments an agent posted as *internal notes* (`public: false`) that were meant to be public replies, within the last N days. Produces a Markdown report for human review plus a JSON report a future republish script can consume without re-scraping. Writes nothing to Zendesk. |

### Not yet built (deliberately)

A **republish / write-fix** script that turns the findings into real public
replies. It is gated on a human reviewing the `find_internal_notes.py` report
first, and will live here as its own clearly-named write-capable file.
