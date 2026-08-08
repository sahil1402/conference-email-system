# Historical Ticket Mining — Marc Workflow Analysis

## Purpose
Mine historical AAAI-27 tickets to learn how questions were actually resolved,
grouped by intent, to make the workflow-suggestion feature data-driven (currently
hand-authored) and to explore a future retrieval step in the pipeline that
surfaces a matched historical workflow for new tickets — improving both workflow
suggestions and auto-draft quality.

## Status
- Step 1 — Locate & define corpus: **COMPLETE**
- Step 2 — Stage 1 extraction (small test batch): not started
- Step 3 — Stage 1 extraction (full corpus): not started
- Step 4 — Stage 2 grouping by intent + workflow-pattern count: not started

## Data Source
- Zendesk export via OAuth client `confmail` (aaai.zendesk.com), pulled 2026-07-16 UTC
- 21,219 total tickets, 15,878 users, 45,659 comment events
- Location: `data/tickets/` — **gitignored, contains real user PII, never committed or shared as raw files**

## Marc's Corpus Definition
- File: `marc_threads.jsonl`
- Selection rule (per export manifest): tickets where Marc (user `39818737387419`, `pujol@aaai.org`) authored **≥1 public reply**
- 4,094 threads — 19.3% of the 21,219 total tickets
- **Decision:** use all 4,094 threads as-is. No filtering by ticket ownership (`assignee_id`) or by whether other agents also replied. "Marc replied" is the criterion that matters for this analysis — not "Marc owned the ticket."

## Verified Data Quality
- Threads are **full, complete conversations** — cross-checked comment-by-comment against `comment_events.jsonl`: 0 truncated, 0 extra, across all 4,094 threads
- 10,577 total comments, average 2.58 per thread, range 1–10
- Both sides of the conversation present: 5,180 requester comments, 5,161 Marc comments, 236 from other agents
- Internal (non-public) notes included: 394 of 10,577 comments, of which 334 are Marc's
- For context only (not used to filter): Marc is the assigned owner on 4,024/4,094 threads; 136 threads (3.3%) also have a public reply from another agent
- Zendesk's built-in demo ticket (id `1`, 2021-07-13, "Sample ticket: Meet the ticket") **confirmed absent** from `marc_threads.jsonl` — verified by subject/tag search across all 21,219 tickets, not just an ID lookup. Marc's corpus has a hard floor at ticket `12449` / 2024-08-09, spanning **2024-08-09 to 2026-07-16 (~2 years)**, not the export's full 5-year range

## Open Items / Caveats
- `state.json` shows 46,075 vs. the manifest's 45,659 comments — explained as pre-dedup counter vs. final deduped file; the file itself is internally consistent, not a data-quality issue
- All raw ticket data contains real user PII — must stay gitignored, never committed, never shared outside this analysis

## Next Step
Stage 1: one LLM call per ticket extracting three fields —
`what_was_asked`, `steps_taken`, `resolution`.
Will be tested on a small batch (20–30 threads) before scaling to all 4,094.
