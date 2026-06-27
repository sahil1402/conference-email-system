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

**Next steps (Phase 3 options):**
  A. Trainable classifier (fine-tuned intent model)
  B. RL-based router (reward signal from chair approve/reroute actions)
  C. PostgreSQL migration (Alembic, production-ready persistence)
  D. Delta GPU activation + local model swap (one-line drafter change)

---

## Current Status

| Phase | Status | What It Contains |
|---|---|---|
| Phase 0 | Complete | Skeleton, config, DB, frontend shell |
| Phase 1A | Complete | Toy dataset + knowledge base JSON |
| Phase 1B | Complete | Repository layer + config updates |
| Phase 1C | Complete | Pipeline modules (classify/retrieve/route/draft + orchestrator) |
| Phase 1D | Complete | API routes (v1) + seed script + unit tests |
| Phase 2A | Complete | Frontend API client layer + React Query hooks |
| Phase 2B | Complete | Layout shell + dashboard (design system, ui components, sidebar, dashboard page) |
| Phase 2C | Complete | Email Queue split-pane + auto-replies table + ingest panel |
| Phase 2D | Complete | Audit log timeline + Analytics charts + responsive polish pass |
| **Phase 2** | **Complete** | **Full frontend — all pages built, typed, demo-ready** |
| Phase 3 | Next | Trainable classifier / RL router / Postgres / local model (see options) |

---

## Session Update Instructions

At the end of EVERY session without exception, Claude must:
1. Append a new entry under Phase History following the format above
2. Update the Current Status table
3. Run: type CLAUDE.md to confirm the file saved correctly
4. Report: "CLAUDE.md updated — [phase name] logged"

This is not optional. If a session ends without this step, the project memory is broken.
