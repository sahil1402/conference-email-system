# Layered Knowledge Base — Design

Status: **DRAFT for review** · Date: 2026-07-18 · Depends on: `main` @ `1b0f26f` (PostgreSQL migration)

## 1. Problem

AAAI policy changes throughout a cycle, and **internal policies that never appear
on the public webpage** (chair rulings, deadline extensions, clarifications) come
up as the conference progresses. Two concrete failures today:

1. **No home for internal policy.** The KB (`policy_documents`) only models
   scraped public policy: `policy_key, title, content, category, tags, source`
   (`backend/app/db/models.py:117`). There is no way to mark a chunk as internal,
   nor to retire one when it changes.
2. **Two sources of truth that drift, and even the two retrievers disagree.**
   - BM25 (`PolicyRetriever._ensure_loaded`, `retriever.py:67`) indexes the flat
     file `data/knowledge_base/policies.json`.
   - FAISS (`FAISSRetriever._load_policies`, `faiss_retriever.py:62`) indexes the
     `policy_documents` **DB table** via `PolicyRepository`.
   - `fusion` (production default) therefore fuses a file-backed ranker with a
     DB-backed ranker. Seeding loads the JSON into the DB, but nothing keeps them
     in sync afterward.

This is also the retrieval ceiling: E003 shows `hit@3 == hit@5 == .892` — when a
gold chunk exists it is already top-3; the residual ~11% miss are chunks **absent
from the KB**. Fixing coverage (this doc) matters more than retrieval depth.

## 2. Decisions (locked)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Visibility tiers | **`public` + `internal`** (two tiers; no separate `chair_only`) |
| 2 | Temporal model | **Current truth + audit** — no "as-of-date" history / version chains |
| 3 | Retriever source | **Read the DB**; retire `policies.json` as the retrieval source |

### 2.1 What `internal` means (confirm)

Both tiers are **retrievable and usable to answer requesters** — internal policy
exists precisely so the system can answer questions the public site does not
cover (e.g. "the deadline was extended"). `internal` is a **provenance marker**,
not a send-block:

- **`public`** — from the official AAAI corpus. Freely citable as settled policy.
- **`internal`** — chair-authored, not on the public site. Retrievable and
  citable, but tagged internal for audit and drafter awareness. Whether a given
  internal ruling must be confirmed by a chair before it is stated to a requester
  stays with the **existing `[CHAIR: …]` placeholder contract** (`drafter.py`) —
  visibility does not force it, and does not hard-block internal text from a reply.

> The alternative reading — `internal` = chair-guidance that is *never* sent to a
> requester — is **not** what this spec assumes. If that is what you want, say so
> and §6 changes (internal chunks would feed only `notes_for_chair`).

## 3. Architecture

One store, two layers distinguished by data — no second file, no second index,
no prompt-injection path.

```
              ┌───────────────────────── policy_documents (Postgres/SQLite) ──────────────────────────┐
 AAAI source  │  source='aaai_scrape'  visibility='public'   status='active'   ← public layer         │
  .md files ─►│  ...                                                                                    │
 (importer)   │  source='chair:<id>'   visibility='internal' status='active'   ← internal overlay      │
 chairs ─────►│  source='aaai_scrape'  visibility='public'   status='inactive' (retired, not indexed)  │
 (admin API)  └───────────────────────────────────────────────────────────────────────────────────────┘
                                        │  PolicyRepository.list_for_index(visibilities=('public','internal'))
                                        │      → status='active' only
                                        ▼
                     BM25 + FAISS  (both read the SAME repository query)
                                        │   fusion RRF over the two
                                        ▼
                     retrieve(query, intent, top_k)  →  drafter grounding + citations
```

Change is contained to five units, each with one job:

### 3.1 Schema (Alembic migration)
Follow the existing pattern (`b8d3f6a1c204_phase_e_policy_tags_source`). Add to
`policy_documents` (`backend/app/db/models.py:117`):

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `visibility` | `String(16)`, indexed, not null | `'public'` | `public` \| `internal` |
| `status` | `String(16)`, indexed, not null | `'active'` | `active` \| `inactive` (soft on/off) |

Backfill: all 93 existing rows → `visibility='public'`, `status='active'`
(`source` already carries the origin doc). No `version`, `supersedes_id`,
`effective_at`, or `expires_at` — decision #2 keeps history in `audit_logs`, not
in extra columns.

### 3.2 Repository (`policy_repository.py`)
DB access stays here (architecture rule 5 — no raw SQL in the pipeline). Add:

- `list_for_index(db, visibilities=("public","internal")) -> list[PolicyDocument]`
  — the single filtered query (`status='active'` AND `visibility IN …`). Both
  retrievers call this, so the filter lives in exactly one place.
- `upsert_by_key(db, key, **fields)` — idempotent insert-or-update for the public
  importer.
- `create_internal(db, *, actor, ...)` and `retire(db, key)` (sets
  `status='inactive'`) — the internal-authoring path.

### 3.3 Retriever — unify BM25 onto the DB (`retriever.py`)
`PolicyRetriever` currently loads a file synchronously. Refactor its load to
mirror FAISS (`faiss_retriever.py:62-64`): fetch via
`PolicyRepository.list_for_index` in a short-lived async session. `retrieve()` is
already `async`; make `_ensure_loaded` async and `await` it there.
`rebuild_index()` (`retriever.py:78`) already exists as the cache-clear seam.
`policies.json` becomes a scrape **import artifact only** — no longer read at
query time.

**Overriding a public policy** (`policy_key` is UNIQUE — `models.py:123` — so
rows never share a key): a chair **retires** the stale public row
(`status='inactive'`) and **adds** an `internal` row (its own key) with the
current ruling. The filter (`status='active'`) drops the retired row, so only the
new ruling is indexed. Amending in place (editing a public row's text) is allowed
too, but see the importer-ownership rule (§3.4) so a re-scrape does not clobber it.

### 3.4 Public importer (`scripts/chunk_policies.py` → `scripts/seed_real_policies.py`)
Make seeding **idempotent upsert-by-`policy_key`** with `source='aaai_scrape'`,
`visibility='public'`. Re-running after AAAI edits the site refreshes the public
layer and leaves the internal overlay untouched.

**Field ownership (prevents re-scrape resurrection):** the importer owns only the
**content** fields (`title`, `content`, `category`, `tags`) of `aaai_scrape`
rows. It **never** writes `status` or `visibility` — those are chair-owned
governance fields. So a public policy a chair retired (`status='inactive'`) stays
inactive across re-scrapes, and the importer never touches internal rows. New keys
on the site are inserted `active`/`public`; keys removed from the site are left
as-is (retire manually if desired) rather than auto-deleted.

### 3.5 Internal authoring (`app/api/v1/policies.py`, new)
`POST /api/v1/policies` — a chair adds an internal policy (`visibility='internal'`,
`source='chair:<id>'`); `PATCH /api/v1/policies/{key}/retire` sets one inactive.
Every write goes through the existing `audit_logs` (actor, action, before/after)
— that is the entire history model (decision #2) — and then fires
`rebuild_index()` so the live index reflects the change **with no restart**. This
is the "constantly changing KB" mechanism, built on the seam that already exists.

### 3.6 KB governance & update flow
Governance is **chair-declared, system-assisted** — the system surfaces what looks
related, the chair decides. It never auto-decides that a new policy contradicts an
old one (auto-retiring official policy is too risky). When a chair adds an internal
policy via §3.5:

1. **Similarity check (assist).** The new text is run through the existing `fusion`
   retriever against the active KB → top-k most-similar existing policies are shown
   to the chair. Dogfoods the retriever; no new component.
2. **Chair declares the relation (govern).**
   - **Overrides an old policy** → chair marks the related policy(ies) to retire.
     Those flip to `status='inactive'`; the new row inserts `active`/`internal`.
     Retrieval returns only the new ruling; the old text survives as an inactive
     row.
   - **Additive (no contradiction)** → chair confirms "replaces none"; the new row
     just inserts `active`. The KB grows — this is what closes the coverage tail
     that caps `hit@k` (E003), not retrieval depth.
3. **Audit + refresh.** Every create/edit/retire/reactivate writes an `audit_logs`
   entry (actor from auth, timestamp, before/after) and fires `rebuild_index()`.

Authority & keys: chairs (authenticated — the same account system that will
populate `[Sender name]`) own internal rows and the `status`/`visibility` fields;
the importer owns public content (§3.4). Internal rows get generated keys
(`int_<slug>`, a uniqueness counter appended on collision) so they never collide
with scrape keys (`policy_NNN`).

**Residual risk (named):** two *active* policies that quietly contradict each other
(a chair added a new one without retiring the old) → the drafter gets conflicting
grounding. The authoring-time similarity check is the guardrail; beyond it the
chair is the authority. No automated contradiction detection.

## 4. Retrieval strategy is unchanged

This rework changes the corpus *source* and adds a *filter*, not the retrieval
strategy. `QUERY_STRATEGY=distill` (one LLM call → queries + intent) and
`RETRIEVAL_BACKEND=fusion` (RRF over BM25 + FAISS) stay exactly as they are. The
only retrieval-side effect is a **correctness gain**: both rankers now read the
same DB corpus through `list_for_index`, so the visibility/status filter is applied
once, upstream of both rankers and the fusion — ending the current file-vs-DB skew
between BM25 and FAISS. (Score-floor / dynamic-`k` remains a separate,
out-of-scope retrieval-quality lever.)

## 5. Visibility → citation

Retrieval returns `public` + `internal` active chunks; the drafter may cite both.
Internal `policy_NNN`/internal keys are scrubbed from requester-facing text by the
**existing** id-scrub in the placeholder contract (`drafter.py`) — extend it to
cover internal key prefixes. Nothing internal auto-releases: every send still
passes `send_gate.authorize_send` with `ALLOW_AUTO_SEND=False`.

## 6. Postgres notes (from PR #2)

- `visibility`/`status` are indexed columns → cheap `WHERE` filters on both
  SQLite (dev) and Postgres (prod).
- Any JSON metadata query uses the dialect-agnostic accessor pattern
  (`Email.routing["lane"].as_string()`, `email_repository.py:41`), never
  `func.json_extract`.
- Alembic runs over the single async `DATABASE_URL`; the migration is one file in
  the existing chain.

## 7. Testing

Hermetic (in-memory SQLite via the existing conftest). New/updated:
- `policy_repository`: `list_for_index` filters status+visibility; `upsert_by_key`
  insert vs update; `supersede` flips status.
- `retriever`: BM25 now reads the DB; a `visibility='internal'` active row is
  retrievable; a `status='inactive'` row is not.
- migration: upgrade adds columns + backfills 93 rows to `public`/`active`.
- `policies` endpoint: create writes an `internal` row + audit entry + rebuild;
  retire flips a public row to `inactive` + audit; a follow-up re-scrape does not
  reactivate it (field-ownership rule).
- governance flow: the create path's similarity check surfaces related active
  policies; retiring the marked one removes it from the next `list_for_index`.
- fusion: BM25 and FAISS now index the same set (no source skew).

## 8. Out of scope (YAGNI)

`chair_only` tier · version chains / `supersedes_id` · `effective_at`/`expires_at`
scheduling · "as-of-date" historical queries · **automated contradiction/conflict
detection** (governance is chair-declared, system-assisted — §3.6) · any
prompt-injection layer (overlay rows are retrieved and cited properly, so injection
is unnecessary).

## 9. Rollout

1. Migration (columns + backfill) — safe, additive.
2. Repository methods + tests.
3. Switch BM25 to the DB; verify `fusion` parity on the 93-chunk corpus.
4. Idempotent importer.
5. Internal-authoring endpoint + audit + `rebuild_index()`.
6. Re-run E003 retrieval eval; add a few internal rows for the known coverage-tail
   tickets and confirm they now retrieve.
