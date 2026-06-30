# CLAUDE.md — Conference Email System
# Project memory. Read this fully at the start of every session before writing any code.

---

## Project Overview

AI-powered conference email management platform for AAAI, NeurIPS, ICML, ICLR.

Two-lane workflow:
- FAQ Lane: High-confidence emails auto-replied using retrieved policy text only
- Human Review Lane: Ambiguous/sensitive emails get AI draft + chair approval workflow

---

## Collaborators

- Sahil — lead developer
- Prof. Yan — PI / stakeholder
- Jiacheng — collaborator (constraint: no specific model names in any design documents)

---

## Project Path

D:\USC\The Melady Labs\conference-email-system

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 14 + TypeScript + Tailwind CSS v3 + shadcn/ui |
| Backend | Python + FastAPI + async SQLAlchemy |
| Database | SQLite via Alembic migrations |
| AI | Anthropic API (provider swappable via config) |
| Retrieval | BM25 via rank_bm25 (swappable via config) |
| Testing | pytest + pytest-asyncio |

Dependencies are managed via `backend/pyproject.toml` (there is no `requirements.txt`).

---

## Architecture Rules (Non-Negotiable)

These 6 modules must always stay separate and independently replaceable:

1. Classifier — intent + confidence score
2. Retriever — policy chunk lookup
3. Router — lane decision (faq vs human_review)
4. Drafter — AI reply generation
5. Persistence Layer — DB access via repositories only, never raw SQL in pipeline
6. UI Layer — Next.js frontend, never mixed with backend logic

---

## Config Flags (backend/app/core/config.py)

Typed via pydantic-settings `Settings`. Values load from environment, then a local `.env`.
The first four are the architectural "swappable seams" that let pipeline modules be replaced
without rewriting the app. Access pattern: `from app.core.config import settings`
(cached singleton via `get_settings()`).

| Flag | Purpose | Default |
|---|---|---|
| MODEL_PROVIDER | AI provider for the drafter (`anthropic_api` \| `local`) | `anthropic_api` |
| CONFIDENCE_THRESHOLD | Min classifier confidence to qualify for the FAQ auto-reply lane | `0.75` |
| RETRIEVAL_BACKEND | Retriever implementation (`bm25` \| `vector`) | `bm25` |
| ROUTING_STRATEGY | Router decision policy (`rule_based` \| `rl`) | `rule_based` |
| ANTHROPIC_API_KEY | Secret for the Anthropic provider | `None` |
| DATABASE_URL | DB connection string (normalized to aiosqlite at runtime) | `sqlite:///./conference_email.db` |
| FAQ_CONFIDENCE_THRESHOLD | Min confidence the router applies for FAQ auto-reply (Phase 1B) | `0.65` |
| MAX_RETRIEVED_CHUNKS | Max policy chunks retrieved for grounding | `3` |
| DRAFTER_MAX_TOKENS | Max tokens for drafter generation | `500` |
| DRAFT_MODEL | Drafter model id (read by drafter; no hardcoded model names) | `claude-opus-4-8` |

---

## Database Tables

ORM models live in `backend/app/db/models.py` (the persistence layer). Pipeline outputs
(classification, routing, draft) are stored as JSON columns on the `emails` row for the MVP
and can be normalized into their own tables later without changing the Pydantic contracts.

| Table | Model Class | Purpose |
|---|---|---|
| emails | Email | Incoming conference email + full lifecycle state (status, classification/routing/draft JSON) |
| audit_logs | AuditLog | Append-only record of actions taken on an email (actor, action, metadata) |
| policy_documents | PolicyDocument | FAQ / policy knowledge-base entries used to ground replies |

Migrations: `cd backend && alembic upgrade head`
(Alembic env: `backend/migrations/`; initial migration: `988d40d1a9ee_initial_schema.py`)

---

## Folder Structure (current files listed per-phase below)

```
conference-email-system/{CLAUDE.md, README.md, LICENSE}
data/emails/toy_dataset.json (30 emails) · data/knowledge_base/policies.json (45 chunks)
backend/  pyproject.toml (deps, no requirements.txt) · alembic.ini (script_location=migrations/) · main.py (FastAPI root + /health)
  migrations/{env.py, versions/988d40d1a9ee_initial_schema.py} · scripts/seed.py · tests/
  app/core/config.py · app/db/{database.py, models.py, repositories/} · app/models/{enums.py, schemas.py}
  app/pipeline/ (classifier/retriever/router/drafter/orchestrator flat .py) · app/api/{routes/ stubs (/api prefix), v1/ (emails, analytics)}
frontend/  package.json (Next.js 14.2.35) · tailwind.config.ts / postcss.config.mjs / components.json
  src/{app/, components/, lib/, hooks/, types/index.ts}
```

---

## Testing Policy

Every pipeline module must have a corresponding test file.
Tests must run without real DB or API connections — mock both. Use pytest + pytest-asyncio.
Run all tests: `cd backend && python -m pytest tests/ -v`

---

## Engineering Rules

Always:
- Read existing code before writing anything
- Keep modules separate and typed
- Use repositories for all DB access
- Write tests for every pipeline module
- Update this file at the end of every session

Never:
- Mix frontend and backend logic
- Hardcode model names anywhere in source code
- Create monolithic files
- Skip the CLAUDE.md update at end of session

---

## How to Run

```
# Backend
cd backend
pip install -e .            # deps from pyproject.toml (no requirements.txt)
alembic upgrade head
uvicorn main:app --reload   # app entry is backend/main.py

# Frontend
cd frontend
npm install
npm run dev

# Seed database
cd backend && python scripts/seed.py

# Run tests
cd backend && python -m pytest tests/ -v
```

---

## Phase History

### Phase 0 — Complete
- backend: main.py (FastAPI root: CORS, lifespan stub, router registration, /health); app/core/config.py (Settings + 4 flags + get_settings()); app/db/{database.py (async engine, async_session_factory, get_db, Base), models.py (Email, AuditLog, PolicyDocument)}; app/models/{enums.py (EmailIntent, RoutingLane, SensitivityLevel, EmailStatus, UserRole), schemas.py (Pydantic v2: EmailIn, IntentMatch, ClassificationResult, …)}; app/pipeline/{classifier,retriever,router,drafter}/ + app/api/routes/{emails,dashboard,auto_replies,audit}.py stubs (/api prefix); alembic.ini + migrations/env.py + versions/988d40d1a9ee_initial_schema.py; pyproject.toml (fastapi, uvicorn, pydantic[email], pydantic-settings, sqlalchemy, alembic, anthropic, rank-bm25, aiosqlite, pytest)
- frontend: Next.js 14 shell (src/app, components, lib/utils, types/index, tailwind/postcss/components.json)
- Verified: /health 200; alembic upgrade head clean. Commits: b93d751 (scaffold), bf1d035 (README), 946ffab (LICENSE).

### Phase 1A — Complete
- data/emails/toy_dataset.json — 30 labeled emails (submission_deadline/formatting_requirements/general_inquiry/review_assignment/submission_withdrawal/ethics_concern ×4, authorship_dispute/technical_issue ×3; lanes 12 faq / 18 human_review)
- data/knowledge_base/policies.json — 45 chunks (formatting_requirements/review_process/ethics_policy/authorship_guidelines ×7, submission_deadlines/withdrawal_policy ×6, general_faq ×5)
- Verified: both JSON parse; distributions meet minimums.

### Phase 1B — Complete  (commit: feat(backend): add repository layer and update config)
- config.py +FAQ_CONFIDENCE_THRESHOLD(0.65)/MAX_RETRIEVED_CHUNKS(3)/DRAFTER_MAX_TOKENS(500); .env.example +same 3 vars; pyproject.toml +python-dateutil>=2.9
- app/repositories/: email_repository.py (EmailRepository: create_email, get_email_by_id, get_emails_by_status, update_email_status, get_email_queue, count_emails_by_status); policy_repository.py (PolicyRepository: get_all_policies, get_policies_by_category, bulk_insert_policies); audit_repository.py (AuditRepository: log_action, get_audit_trail, get_recent_actions)
- Decisions: async select() throughout, writes commit+refresh, reads return None/[] never raise; email_id typed str but PK is int autoincrement → coerce, non-numeric → not-found; update_email_status metadata applied to JSON cols (classification/routing/draft); get_email_queue lane filter via json_extract(routing,'$.lane'); bulk_insert_policies maps id→policy_key, drops extra source/tags; ANTHROPIC_API_KEY kept str|None=None (no duplicate field)
- Verified: repos import clean; config loads; in-memory async smoke test (all 12 methods) pass.

### Phase 1C — Complete  (commit: feat(pipeline): implement classify → retrieve → route → draft)
- config.py +DRAFT_MODEL (default "claude-opus-4-8") — drafter reads settings.DRAFT_MODEL (no hardcoded model id)
- app/pipeline/ flat modules: classifier.py (IntentClassifier + ClassificationResult; keyword-overlap, classify/classify_batch, VALID_INTENTS + KEYWORD_RULES); retriever.py (PolicyRetriever + RetrievedChunk; BM25 over policies.json, retrieve/rebuild_index, lazy cached index, maps id→policy_id, indexes title+content+tags); router.py (EmailRouter + RoutingDecision; sensitive-intent override + threshold/grounding gate, reads FAQ_CONFIDENCE_THRESHOLD); drafter.py (ResponseDrafter + DraftResponse; AsyncAnthropic, no-key/error fallbacks, parses policy_\d+ citations); orchestrator.py (EmailPipeline + PipelineResult; classify→retrieve→route→draft→persist+audit, timing, failure policy)
- Decisions: DELETED empty pipeline/{classifier,retriever,router,drafter}/ stub subpackages (they shadowed the flat modules); drafter omits `thinking` param; PipelineResult.status complete/draft_failed/error → Email.status DRAFT_GENERATED/ROUTED
- Verified: all modules import clean; classifier/router/E2E smoke pass (no API key → retriever 3 chunks, lane=faq, status=complete).

### Phase 1D — Complete  (commit: feat(api): add endpoints, seed script, and unit tests)
- backend/app/api/v1/emails.py (POST /emails/ingest, GET /emails/queue, GET /emails/{id}, PATCH /emails/{id}/approve, PATCH /emails/{id}/reroute); api/v1/analytics.py (GET /analytics/summary, GET /analytics/recent-activity); main.py mounts both under /api/v1 (stub /api routers left intact)
- backend/scripts/seed.py (idempotent policy load + pipeline over toy emails); pyproject.toml +pytest-asyncio>=0.23 + [tool.pytest.ini_options] (asyncio_mode="auto", testpaths=["tests"]); tests/: conftest.py (fixtures) + test_classifier.py (6) + test_router.py (6) + test_retriever.py (4)
- Decisions: lane in routing JSON (routing.lane); confidence/intent in classification JSON; analytics reads JSON keys in Python; classifier KEYWORD_RULES tweaks (ethics_concern +"ethical"/"violation"; general_inquiry −generic "question"/"general"); approve/reroute write lowercase "approved"/"rerouted"; queue total = sum(count_emails_by_status); pending_count = status not in {approved,rerouted}; reroute updates routing.lane + logs reason/new_lane
- Verified: pytest 16 passed; seed 30/30 (6 FAQ, 24 human_review, avg conf 0.730); /health + /api/v1/emails/queue + /analytics/summary + recent-activity OK. Note: ANTHROPIC_API_KEY unset → fallback drafts ("API key not configured"), pipeline status still "complete".

### Phase 2A — API Client Layer — Complete (2026-06-27)
- frontend/.env.local (NEXT_PUBLIC_API_URL); src/types/index.ts (TS types matching backend); src/lib/api/{client.ts (axios + error interceptor), emails.ts (getEmailQueue, ingestEmail, approveEmail, rerouteEmail), analytics.ts (getAnalyticsSummary)}; src/lib/providers.tsx (QueryClientProvider + ReactQueryDevtools); src/hooks/{useEmailQueue.ts (15s poll), useAnalytics.ts (30s poll), useEmailActions.ts (approve/reroute/ingest mutations + cache invalidation)}
- Deps: @tanstack/react-query, @tanstack/react-query-devtools, axios
- Follow-up: reconciled types/API to live backend — lane "faq"/"human_review", uppercase pipeline statuses, nested classification/routing/draft on Email, analytics *_count fields, EmailQueueResponse envelope, PipelineResult ingest return, PATCH for approve/reroute, real request bodies.

### Phase 2B — Layout Shell + Dashboard — Complete (2026-06-27)
- globals.css (dark-mode CSS variable design system, indigo accent #6366f1); components/ui/ (Badge, ConfidenceBar, StatCard, EmptyState, LoadingSpinner, ErrorBanner + ui/index.ts); components/layout/Sidebar.tsx (fixed sidebar, active state, Melady Lab branding); app/layout.tsx (Sidebar + flex layout); app/dashboard/page.tsx (stats row, intent distribution bars, recent emails — live hooks)
- Decisions: renamed sidebar.tsx→Sidebar.tsx (git mv) + components/layout/index.ts barrel; components use var(--token) directly (NOT shadcn Tailwind theme — globals.css redefines --background/--border/--accent as hex, retiring hsl(var(--…)) utilities + unused button.tsx); intent bars normalized to max count (proportional to count); dropped font-sans (Geist) so globals Inter→system-ui applies
- Verified: tsc clean; backend payloads match types; /dashboard 200 (922 modules); SSR shell + loading branch, data on client hydration (CORS open to localhost:3000).

### Phase 2C — Email Queue + Review Interface — Complete (2026-06-27)
- components/email/: EmailListItem.tsx (queue row: lane indicator, avatar, badge, confidence bar, selected/hover), EmailDetail.tsx (split-pane right: classification, policy citations, draft editor, action bar), EmailFilters.tsx (search + lane + status); app/queue/page.tsx (split-pane queue + filtering + approve/reroute mutations); app/auto-replies/page.tsx (FAQ table + stats strip); components/dashboard/IngestPanel.tsx (test email injector + pipeline preview, on dashboard); lib/format.ts (intent label, time ago, date, initials, lane/status labels + badge variants)
- UX: selected email accent left border; draft pre-loaded in textarea; reroute inline form (no modal); IngestPanel collapsed by default
- Decisions: added Email.retrieved_chunks?: RetrievedChunk[]|null (OPTIONAL — backend _email_to_dict doesn't persist chunks, only ingest PipelineResult; EmailDetail falls back to draft.citations); editedDraft+rerouteReason live inside EmailDetail (page keys on email.id to reset); on-color text uses var(--text-primary); status badge map APPROVED/SENT→success, DRAFT_GENERATED→warning, REROUTED→review, else neutral
- Verified: tsc clean; /dashboard,/queue,/auto-replies 200; live mutation round-trip — ingest POST{from,to,subject,body}→PipelineResult (id=31), approve PATCH{approved_by:"chair",final_text}→"approved", reroute PATCH{rerouted_by:"chair",reason,new_lane:"faq"}→"rerouted"+lane "faq" (created emails 31/32 in dev DB); filtering = client-side useMemo over subject+sender / lane / status.

### Phase 2D — Audit Log + Analytics + Polish (complete)

**Date completed:** 2026-06-27

**What was built:**
- types/index.ts: AuditEntry interface
- lib/api/audit.ts: getAuditLog()
- hooks/useAudit.ts: useAudit() with 10s stale time
- app/audit/page.tsx: vertical timeline with action-colored dots,
  collapsible JSON detail blocks, search filtering
- app/analytics/page.tsx: KPI row, donut chart (recharts),
  horizontal intent bar chart, confidence distribution bar chart
- Sidebar: added Analytics nav link (/analytics, BarChart2 icon)
- components/layout/AppShell.tsx: responsive chrome (mobile top bar +
  hamburger + slide-in drawer + backdrop); layout.tsx now renders <AppShell>
- app/*/layout.tsx (dashboard, queue, auto-replies, analytics, audit):
  per-route metadata for page titles (root uses title.template "%s · ConfMail")
- Polish: page titles, responsive sidebar (hamburger <768px),
  spacing consistency (gap-6 sections), micro-transitions, empty state icons

**Dependencies added:** recharts (^3.9.0)

**Chart color note:** recharts SVG fills use hex directly
(#10b981, #f59e0b, #ef4444, #6366f1) — CSS variables do not
resolve inside SVG attributes. They mirror the globals.css tokens.

**Key decisions / deviations:**
- NO backend GET /audit exists (routes/audit.py is an empty stub). The only
  cross-email audit feed is GET /api/v1/analytics/recent-activity, which returns
  {email_id, action, actor, timestamp} for the 20 most recent actions — no row
  id, no metadata. getAuditLog() wires to THAT endpoint and normalizes it into
  AuditEntry (id = feed index, details = {}). Consequence: the timeline shows
  real actions (classified/retrieved/routed/drafted + approved/rerouted) but the
  collapsible "Show details" JSON stays hidden (details always empty) until the
  backend exposes per-action metadata or a real /audit route. Backend was NOT
  modified (per constraint).
- Page titles use per-route server `layout.tsx` files exporting `metadata`
  (the idiomatic App Router approach for client pages) rather than next/head.
- Collapsible panels animate via grid-template-rows 0fr↔1fr + opacity (smooth,
  no magic max-height; children stay mounted so state persists).
- On-color text uses var(--text-primary) (incl. sidebar brand) — no hardcoded
  #fff anywhere now.

**Verified with:**
- npx tsc --noEmit → exit 0 (clean)
- pytest tests/ -q → 16 passed
- next dev: all 6 routes compiled and served HTTP 200
  (/ →307→/dashboard; /dashboard, /queue, /auto-replies, /analytics, /audit →200),
  no errors/warnings in the dev log. /analytics pulled recharts (1960 modules).
- <title> confirmed on all 5 pages ("Dashboard · ConfMail", … "Audit Log · ConfMail").
- Analytics nav link + mobile hamburger present in rendered HTML.
- recent-activity returns 20 real entries (incl. approved/rerouted) → audit
  timeline is populated on client hydration.
- Charts render client-side (recharts); SSR shows the loading branch. Visual
  chart rendering not screenshot-verified (no browser automation here), but the
  page compiles + serves 200 with the data feeds confirmed live.

**Phase 2 is complete.** All frontend pages are built, typed, and demo-ready.

**Next steps (Phase 4):** Deployment + pilot — Docker, CI/CD, live conference integration.

---

## Current Status

| Phase | Status | What It Contains |
|---|---|---|
| Phase 0 | Complete | Skeleton, config, DB, frontend shell |
| Phase 1A | Complete | Toy dataset + knowledge base JSON |
| Phase 1B | Complete | Repository layer + config updates |
| Phase 1C | Complete | Pipeline modules (classify/retrieve/route/draft + orchestrator) |
| Phase 1D | Complete | API routes (v1) + seed script + unit tests |
| **Phase 2** | **Complete** | **Full frontend — all pages built, typed, demo-ready** |
| Phase 2A | Complete | Frontend API client layer + React Query hooks |
| Phase 2B | Complete | Layout shell + dashboard (design system, ui components, sidebar, dashboard page) |
| Phase 2C | Complete | Email Queue split-pane + auto-replies table + ingest panel |
| Phase 2D | Complete | Audit log timeline + Analytics charts + responsive polish pass |
| **Phase 3** | **Complete** | **All 5 sub-phases complete — 36/36 tests** |
| Phase 3E | Complete | Real audit endpoint /api/v1/audit — paginated, filterable |
| Phase 3C | Complete | PostgreSQL migration ready (asyncpg, Alembic checkpoint) |
| Phase 3D | Complete | Local model backend (Ollama-compatible, MODEL_PROVIDER=local) |
| Phase 3A | Complete | Trainable classifier (all-MiniLM-L6-v2 + LogisticRegression) |
| Phase 3B | Complete | RL bandit router (ε=0.15, feedback loop, rl-stats endpoint) |

---

## Session Update Instructions

At the end of EVERY session without exception, Claude must:
1. Append a new entry under Phase History following the format above
2. Update the Current Status table
3. Run: type CLAUDE.md to confirm the file saved correctly
4. Report: "CLAUDE.md updated — [phase name] logged"

This is not optional. If a session ends without this step, the project memory is broken.

---

## Phase 3E — Audit Endpoint Fix
**Status:** Complete
**Date:** 2026-06-29

### What was built
- `audit_repository.py` rewritten: `get_audit_logs`, `get_audit_log_count`, `create_audit_log`
- `GET /api/v1/audit` — paginated, filterable by email_id / action / actor
- `GET /api/v1/audit/{log_id}` — single log lookup with 404
- `AuditLogResponse` Pydantic schema with id, email_id, action, actor, details, created_at
- New test file: `tests/test_audit_endpoint.py` (5 tests)

### What changed
- Removed stub that was normalizing analytics/recent-activity data
- GET /audit now reads directly from audit_logs table

### Test results
- Previous suite: 16/16 passing
- New suite: 21/21 passing

### Notes / deviations from the original plan (resolved during implementation)
- **Repo location**: the audit repository is at `app/repositories/audit_repository.py`
  (NOT `app/db/repositories/`, whose `__init__.py` is empty). New methods were
  added to the existing **class** `AuditRepository` (instance methods) rather than
  as module-level functions, to preserve the pattern used by orchestrator/emails.py
  and avoid breaking those callers. The 3 pre-existing methods (`log_action`,
  `get_audit_trail`, `get_recent_actions`) were kept intact. Also added
  `get_audit_log_by_id` so the single-log route stays repository-only (architecture rule).
- **Schema mapping**: `AuditLog` has no `created_at`/`details` columns. The real
  columns are `timestamp` and `extra_metadata` (DB column literally `"metadata"`).
  `AuditLogResponse` maps them via Pydantic `validation_alias`:
  `created_at` ← `timestamp`, `details` ← `extra_metadata`. Ordering is `timestamp DESC, id DESC`.
- **Mount path**: the audit router was a bare stub mounted at `/api/audit`. To
  expose `/api/v1/audit` it is now mounted under `/api/v1` in `main.py` (alongside
  `emails_router`/`analytics_router`); the old `/api` stub mount was removed. There
  was no analytics import in the backend route to remove (that behavior lived in the frontend).
- **Tests**: existing tests mock the session; the new endpoint tests use a real
  in-memory SQLite DB (StaticPool so all connections share one `:memory:` DB),
  override the `get_db` dependency, seed logs via the repo, and drive the app
  through httpx `ASGITransport`. No real DB file or network. (httpx already
  available; `asgi_lifespan` not needed — app lifespan is empty.)

---

## Phase 3C — PostgreSQL Migration
**Status:** Complete
**Date:** 2026-06-29

### What was built
- asyncpg (0.31.0) + psycopg2-binary (2.9.12) added to pyproject.toml (aiosqlite kept for tests)
- .env.example updated: DATABASE_URL (asyncpg) + SYNC_DATABASE_URL (psycopg2), with a commented SQLite-dev alternative
- SYNC_DATABASE_URL added to Settings (`app/core/config.py`) for Alembic / sync-tooling use
- migrations/env.py: already async (`run_async_migrations` + `async_engine_from_config`); made `render_as_batch` dialect-conditional (SQLite-only) so it won't interfere with PostgreSQL ALTERs
- Alembic checkpoint revision generated: `507ef4c2d805_phase3c_postgres_ready` (empty up/down — zero schema drift)

### How to switch to PostgreSQL
1. Install and start PostgreSQL locally
2. Create database: `createdb confmail`
3. Set in .env: DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/confmail
   (optionally SYNC_DATABASE_URL=postgresql+psycopg2://... for sync tooling)
4. Run: cd backend && alembic upgrade head
5. Run: cd backend && python scripts/seed.py

### Tests
- SQLite in-memory tests unchanged: 21/21 passing
- No live PostgreSQL instance required to run tests

### Notes / deviations from the original plan (resolved during implementation)
- **Config path**: settings live at `app/core/config.py` (not `app/config.py`).
- **Alembic was already async**: env.py already used `run_async_migrations()` /
  `async_engine_from_config` and injected `ASYNC_DATABASE_URL` from settings — no
  conversion needed. Only change: `render_as_batch` is now `True` solely for the
  SQLite dialect (offline checks the URL prefix; online checks
  `connection.dialect.name`).
- **SYNC_DATABASE_URL is not consumed by the async env.py** (which uses asyncpg).
  It's added to Settings + .env.example as documented config for sync tooling /
  psycopg2-based migrations if preferred later. Defaults to SQLite so dev/tests
  with no .env keep working.
- **Install flag**: used a plain venv `pip install` (Windows `.venv`), not
  `--break-system-packages` (that flag is for Linux PEP-668 environments).
- **Seed path**: the seed script is `backend/scripts/seed.py`, not `backend/seed.py`.
- **No `alembic upgrade` run against PostgreSQL** (no live PG in dev). The checkpoint
  migration was verified by `py_compile` + `alembic history`/`heads` (single linear
  head `507ef4c2d805`). The autogenerate diff was run against the at-head SQLite DB
  and came back empty, confirming models match the schema.

---

## Phase 3D — Local Model Integration
**Status:** Complete
**Date:** 2026-06-29

### What was built
- `LOCAL_MODEL_BASE_URL` (default `http://localhost:11434/v1`) + `LOCAL_MODEL_NAME` (default `llama3.1:8b`) added to config
- drafter.py: "local" provider branch — httpx POST to OpenAI-compatible `{base}/chat/completions` (60s timeout, same system+user prompt as Anthropic, parses `choices[0].message.content`)
- Graceful degradation: httpx/parse errors → fallback draft (`model_used="none"`), warning logged, never raises
- GET /api/v1/health/model — live model status badge endpoint (probes `{base}/models` with 3s timeout when provider=local)
- .env.example updated with MODEL_PROVIDER, LOCAL_MODEL_BASE_URL, LOCAL_MODEL_NAME
- New test file: tests/test_drafter_local.py (4 tests)

### How to switch to local model (Delta GPU)
1. SSH into Delta GPU node
2. Install Ollama: curl -fsSL https://ollama.com/install.sh | sh
3. Pull model: ollama pull llama3.1:8b
4. Start server: ollama serve
5. In backend/.env: MODEL_PROVIDER=local
6. Optionally set LOCAL_MODEL_BASE_URL if not localhost

### Tests
- Full suite: 25/25 passing (21 prior + 4 new)

### Notes / deviations from the original plan (resolved during implementation)
- **MODEL_PROVIDER values**: the existing flag was `Literal["anthropic_api", "local"]`.
  Widened to `["anthropic_api", "anthropic", "local", "fallback"]` — both anthropic
  spellings map to the Anthropic backend, so the historical default `anthropic_api`
  still works while the plan's `anthropic`/`fallback` are now valid. Default kept at
  `anthropic_api`.
- **Drafter was not actually provider-aware**: `draft()` previously ignored
  `self.provider` and branched only on `ANTHROPIC_API_KEY`. Now it branches on
  `self.provider` (anthropic/anthropic_api → Anthropic, local → httpx, else →
  fallback), via new `_draft_anthropic` / `_draft_local` helpers + a shared
  `_fallback()`. Public interface (`__init__(provider=...)`, `draft(...)`) unchanged;
  the orchestrator already passes `settings.MODEL_PROVIDER`.
- **Health endpoint placement**: the plan named `routes/analytics.py`, which doesn't
  exist (the implemented analytics router is `app/api/v1/analytics.py` with prefix
  `/analytics`). No existing v1 router's prefix can yield `/api/v1/health/model`, so
  the endpoint is defined app-level in `main.py` (mirroring the existing `/health`
  probe) — honors "no new router file" and the exact required path. The endpoint
  normalizes `anthropic_api` → `anthropic` in its `provider` field.
- **httpx**: promoted from a dev-only dependency to a main dependency in pyproject
  (already installed in the venv). Installed via plain venv `pip` earlier (Phase 3C),
  not `--break-system-packages` (Windows venv, not Linux PEP-668).
- **Tests**: HTTP fully mocked via `monkeypatch` of `drafter.httpx.AsyncClient`
  (raising client → fallback path; OK client → parse/citation path) + ASGITransport
  for the health endpoint. No real Anthropic or local-model calls.

---

## Phase 3A — Trainable Classifier
**Status:** Complete
**Date:** 2026-06-29

### What was built
- `backend/app/pipeline/trainable_classifier.py`: TrainableClassifier class
  - Embedding: all-MiniLM-L6-v2 (sentence-transformers, CPU-only, lazy-loaded)
  - Model: LogisticRegression(max_iter=1000, C=1.0) (sklearn), saved via joblib
  - Fallback: delegates to `keyword_classify` when no artifact on disk (embedder not loaded)
  - Singleton pattern: `get_trainable_classifier()` (instantiated once at module level)
- CLASSIFIER_BACKEND flag added (Literal["keyword","trainable"], default "keyword")
- `POST /api/v1/train/classifier` endpoint (min 5 samples → 422, returns accuracy)
- Model artifacts saved to: backend/models/ (resolved from __file__, cwd-safe)
- New test file: tests/test_trainable_classifier.py (5 tests)

### How to activate
In .env: CLASSIFIER_BACKEND=trainable
Then POST to /api/v1/train/classifier with labeled email data.
Until trained, falls back to keyword classifier automatically.

### Tests
- Full suite: 30/30 passing (25 prior + 5 new)

### Notes / deviations from the original plan (resolved during implementation)
- **No CLASSIFIER_BACKEND existed**: the classifier was selected via
  `IntentClassifier(strategy="keyword")` (orchestrator hardcoded "keyword"). Added
  the flag to config and wired the orchestrator to
  `IntentClassifier(strategy=settings.CLASSIFIER_BACKEND)`.
- **Interface preserved via refactor**: the public interface is still the async
  `IntentClassifier.classify(email_text, subject)` + `classify_batch`. The keyword
  scoring was extracted into a module-level sync `keyword_classify(subject, body)`
  (no logic duplicated); `IntentClassifier.classify` now dispatches — `trainable`/
  `trained` → `get_trainable_classifier().classify(subject, email_text)`, else
  `keyword_classify`. `TrainableClassifier.classify(subject, body)` is sync per the
  plan; the async wrapper adapts arg order.
- **`method` field added** to `ClassificationResult` (optional, default "keyword";
  trained path sets "trained_classifier"). Backward-compatible — existing
  tests/contracts unaffected.
- **Artifact paths**: class attrs `MODEL_PATH`/`LABEL_PATH` keep the plan's display
  strings (surfaced in the API response), but real joblib IO uses absolute paths
  anchored on `Path(__file__).parents[2]/"models"` so it works regardless of cwd
  (tests run from backend/, where the literal "backend/models/..." would have been wrong).
- **Install**: sklearn/sentence-transformers/joblib (+ torch 2.x **cpu**) installed
  into the Windows `.venv` with plain `pip`, not `--break-system-packages`.
- **Endpoint**: defined in `app/api/routes/training.py` (router prefix `/train`,
  mounted under `/api/v1` → `/api/v1/train/classifier`); training runs in a
  threadpool (`run_in_threadpool`) since it's CPU-bound; 422 via Pydantic
  `min_length=5`; 500 with "Training failed: …" on error.
- **Test isolation**: tests redirect the module's `_MODELS_DIR`/`_MODEL_FILE`/
  `_LABEL_FILE` globals + reset the singleton via `monkeypatch` to a `tmp_path`, so
  the repo's `backend/models/` is never written. Verified clean after the run. The
  embedder downloads once (~90MB) and is then served from the huggingface cache.

---

## Phase 3B — RL Router
**Status:** Complete
**Date:** 2026-06-29

### What was built
- `backend/app/pipeline/rl_router.py`: RLRouter — epsilon-greedy bandit
  - Arms: auto_reply | human_review, per-intent {wins, trials} state
  - Epsilon: 0.15 (15% exploration); optimistic init (win-rate 0.5 for untried arms)
  - Hard guards (checked before the bandit): sensitive intent → human_review; confidence < 0.4 → human_review; confidence < threshold → human_review
  - Persistence: backend/models/rl_router_state.json (human-readable JSON)
  - Singleton: get_rl_router()
- ROUTING_STRATEGY already included "rl" (no config change needed)
- Feedback loop: approve/reroute endpoints call record_feedback() (approve→reward approved lane; reroute→penalize original lane)
- GET /api/v1/analytics/rl-stats — per-intent win rates
- New test file: tests/test_rl_router.py (6 tests)

### How to activate
In .env: ROUTING_STRATEGY=rl
Router immediately starts learning from approve/reroute actions.
State persists across restarts in backend/models/rl_router_state.json.

### Tests
- Full suite: 36/36 passing (30 prior + 6 new)

### Notes / deviations from the original plan (resolved during implementation)
- **ROUTING_STRATEGY already had "rl"** — Step 3a was a no-op.
- **Interface preserved**: public `EmailRouter.route(classification, retrieved_chunks)
  -> RoutingDecision` is unchanged. When `strategy == "rl"`, it lazily imports
  `get_rl_router()` (lazy avoids a circular import: rl_router imports RoutingDecision
  from router) and delegates with extracted `(intent, confidence, threshold)`.
- **Arm vs lane vocabulary**: bandit arms are `auto_reply`/`human_review`; the stored
  lane is `faq`/`human_review`. `RoutingDecision.lane` stays `faq` (downstream
  unchanged); `record_feedback` normalizes `faq` → `auto_reply` so feedback and
  routing agree.
- **Additive safety**: the RL path *keeps* the sensitive-intent override and adds the
  spec's hard confidence floor (0.4) — checked before the bandit, so learning never
  overrides safety. (With the default threshold 0.65 the floor is only independently
  observable when threshold < 0.4, but it's implemented as a first-class guard so the
  floor test holds regardless of threshold.)
- **Email field access**: there is no `email.classification_result`/`email.lane`;
  the feedback helper reads `email.classification["intent"]` and
  `email.routing["lane"]` (JSON columns). For reroute, feedback uses the *original*
  lane (from `existing.routing` before the update) — the action being penalized.
- **Endpoint locations**: approve/reroute are in `app/api/v1/emails.py` (not
  `routes/emails.py`, which is a stub); the rl-stats route was added to
  `app/api/v1/analytics.py` (prefix `/analytics`) → `/api/v1/analytics/rl-stats`.
- **Feedback is best-effort**: both calls go through `_record_rl_feedback`, wrapped
  in try/except + logged — it can never break the chair's approve/reroute action.
- **STATE_PATH**: class attr keeps the display string `backend/models/rl_router_state.json`;
  actual IO resolves it fresh each call (relative → anchored to project root,
  parents[3]; absolute → used as-is), so tests monkeypatch `STATE_PATH` to a tmp file
  and reset the singleton. Repo `backend/models/` verified clean after the run.

**Phase 3 is fully complete** (3A trainable classifier, 3B RL router, 3C PostgreSQL,
3D local model, 3E audit endpoint).

---

## Phase 4A — FAISS Vector Retrieval [COMPLETE]
- Added FAISSRetriever using sentence-transformers (all-MiniLM-L6-v2) + faiss-cpu
- IndexFlatIP with L2 normalization (cosine similarity)
- Lazy index build on first retrieve() call
- rebuild_index() method for live reindexing after policy doc updates
- Factory pattern in retriever __init__.py: RETRIEVAL_BACKEND=faiss | bm25
- New config field: FAISS_MODEL_NAME
- New endpoint: GET /api/v1/retrieval/info
- 6 new tests — all passing
- BM25 unchanged and still default
- Next: Phase 4B — Eval harness with ground truth labels

### Notes / deviations from the original plan (resolved during implementation)
- **No `retriever/` package** — it's a flat module `app/pipeline/retriever.py` (the
  subpackage was deleted in Phase 1C because it shadowed the module). So
  `FAISSRetriever` lives in flat `app/pipeline/faiss_retriever.py`, and the
  `get_retriever()` factory was added to `retriever.py` (not a `retriever/__init__.py`).
- **RetrievedChunk contract kept identical** — fields are `policy_id, title, content,
  score, category, tags` (not the plan's `chunk_id/text/source/intent`), so FAISS is a
  true drop-in for BM25. `content` is the chunk text, `policy_id` the identifier;
  `intent` is a passthrough input (logged, not stored on the chunk).
- **DB-backed (option A)**: FAISS loads PolicyDocument rows via `PolicyRepository`
  using its own short-lived async session (`async_session_factory`), so the public
  `retrieve(query, intent, top_k)` signature stays sessionless like BM25's. BM25 still
  reads `data/knowledge_base/policies.json` (unchanged). repo + session_factory are
  constructor-injectable → tests mock them with zero DB access.
- **Factory wiring**: `get_retriever()` is a singleton that rebuilds only when
  RETRIEVAL_BACKEND changes; `bm25`→PolicyRetriever, `faiss`→FAISSRetriever
  (lazy import), else ValueError. The orchestrator now calls `get_retriever()` (was
  constructing PolicyRetriever directly) so the flag actually swaps the backend.
- **Config**: RETRIEVAL_BACKEND Literal is now `["bm25","faiss"]` (dropped the dead
  `"vector"` placeholder; default still `bm25`). Added `FAISS_MODEL_NAME="all-MiniLM-L6-v2"`.
- **Endpoint location**: there's no `routes/pipeline.py`; the info route is a focused
  `app/api/v1/retrieval.py` router (prefix `/retrieval`) mounted under `/api/v1` →
  `/api/v1/retrieval/info`. For FAISS, `document_count`/`index_built` reflect the lazy
  index (0/false until first retrieve); for BM25, count comes from the KB and
  `index_built` is always true.
- **Install**: `faiss-cpu>=1.8` (cp313 Windows wheel, v1.14.3) into the `.venv` with
  plain `pip` (not `--break-system-packages`); `sentence-transformers` was already present.
- **Verification**: full suite 42/42; live `curl /api/v1/retrieval/info` →
  `{"backend":"bm25","document_count":45,"model_name":null,"index_built":true}`.
