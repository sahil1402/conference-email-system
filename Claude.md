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
without rewriting the app.

| Flag | Purpose | Default |
|---|---|---|
| MODEL_PROVIDER | AI provider for the drafter (`anthropic_api` \| `local`) | `anthropic_api` |
| CONFIDENCE_THRESHOLD | Min classifier confidence to qualify for the FAQ auto-reply lane | `0.75` |
| RETRIEVAL_BACKEND | Retriever implementation (`bm25` \| `vector`) | `bm25` |
| ROUTING_STRATEGY | Router decision policy (`rule_based` \| `rl`) | `rule_based` |
| ANTHROPIC_API_KEY | Secret for the Anthropic provider | `None` |
| DATABASE_URL | DB connection string (normalized to aiosqlite at runtime) | `sqlite:///./conference_email.db` |

Access pattern: `from app.core.config import settings` (cached singleton via `get_settings()`).

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

Migrations: cd backend && alembic upgrade head
(Alembic env: `backend/migrations/`; initial migration: `988d40d1a9ee_initial_schema.py`)

---

## Folder Structure

```
conference-email-system/
├── CLAUDE.md
├── README.md
├── LICENSE
├── data/
│   ├── emails/toy_dataset.json            # 30 labeled toy emails
│   └── knowledge_base/policies.json       # 45 policy/FAQ chunks
├── backend/
│   ├── pyproject.toml                     # deps (no requirements.txt)
│   ├── alembic.ini                        # script_location = migrations/
│   ├── main.py                            # FastAPI app entry (composition root) + /health
│   ├── migrations/
│   │   ├── env.py
│   │   └── versions/988d40d1a9ee_initial_schema.py
│   ├── app/
│   │   ├── core/config.py                 # typed settings + swappable flags
│   │   ├── db/
│   │   │   ├── database.py                # async engine, session factory, get_db, Base
│   │   │   ├── models.py                  # ORM: Email, AuditLog, PolicyDocument
│   │   │   └── repositories/              # (empty — arrives Phase 1B)
│   │   ├── models/
│   │   │   ├── enums.py                   # EmailIntent, RoutingLane, SensitivityLevel,
│   │   │   │                              #   EmailStatus, UserRole
│   │   │   └── schemas.py                 # Pydantic v2 contracts (EmailIn, ClassificationResult, ...)
│   │   ├── pipeline/                      # classifier/ retriever/ router/ drafter/ (stubs)
│   │   └── api/routes/                    # emails, dashboard, auto_replies, audit (stubs, /api prefix)
│   └── tests/                             # __init__.py only (no test files yet)
└── frontend/
    ├── package.json                       # Next.js 14.2.35
    ├── tailwind.config.ts / postcss.config.mjs / components.json
    └── src/
        ├── app/                           # layout.tsx, page.tsx, dashboard/page.tsx, globals.css
        ├── components/                    # layout/sidebar.tsx, ui/button.tsx, dashboard/, email/, pipeline/
        ├── lib/utils.ts
        └── types/index.ts
```

Note: dependencies are managed via `backend/pyproject.toml` (there is no `requirements.txt`).
The `backend/app/pipeline/*` and `backend/app/db/repositories/` directories are currently
stubs/empty and get implemented in Phase 1B–1C.

---

## Testing Policy

Every pipeline module must have a corresponding test file.
Tests must run without real DB or API connections — mock both.
Use pytest + pytest-asyncio.

Test files: backend/tests/  (currently only `__init__.py` — test files arrive with Phase 1C/1D)
Run all tests: cd backend && python -m pytest tests/ -v

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

# Seed database (available after Phase 1D — scripts/seed.py not yet created)
cd backend && python scripts/seed.py

# Run tests
cd backend && python -m pytest tests/ -v
```

---

## Phase History

### Phase 0 — COMPLETE

What was built:
- backend/main.py — FastAPI composition root (CORS, lifespan stub, router registration, /health)
- backend/app/core/config.py — typed Settings + 4 swappable flags + cached get_settings()
- backend/app/db/database.py — async SQLAlchemy engine, async_session_factory, get_db, Base
- backend/app/db/models.py — ORM models: Email, AuditLog, PolicyDocument
- backend/app/db/repositories/ — package stub (empty)
- backend/app/models/enums.py — EmailIntent, RoutingLane, SensitivityLevel, EmailStatus, UserRole
- backend/app/models/schemas.py — Pydantic v2 contracts (EmailIn, IntentMatch, ClassificationResult, ...)
- backend/app/pipeline/{classifier,retriever,router,drafter}/ — package stubs
- backend/app/api/routes/{emails,dashboard,auto_replies,audit}.py — APIRouter stubs (/api prefix)
- backend/alembic.ini + backend/migrations/env.py + versions/988d40d1a9ee_initial_schema.py
- backend/pyproject.toml — deps: fastapi, uvicorn, pydantic[email], pydantic-settings,
  sqlalchemy, alembic, anthropic, rank-bm25, aiosqlite, pytest
- frontend/ — Next.js 14 shell: src/app (layout, page, dashboard/page, globals.css),
  components (layout/sidebar, ui/button), lib/utils, types/index, tailwind/postcss/components.json

Verified with:
- /health returns 200
- alembic upgrade head runs clean (backend/conference_email.db present)

GitHub commit: b93d751 "Phase 0 complete: monorepo scaffold"
(followed by bf1d035 "README added", 946ffab "MIT LICENSE added")

---

### Phase 1A — COMPLETE

What was built:
- data/emails/toy_dataset.json — 30 labeled toy emails (intent + expected lane)
- data/knowledge_base/policies.json — 45 policy / FAQ knowledge-base chunks

Data summary (verified this session):
- Emails: 30
  Counter({'submission_deadline': 4, 'formatting_requirements': 4, 'general_inquiry': 4,
           'review_assignment': 4, 'submission_withdrawal': 4, 'ethics_concern': 4,
           'authorship_dispute': 3, 'technical_issue': 3})
  Lanes: 12 faq / 18 human_review
- Policies: 45
  Counter({'formatting_requirements': 7, 'review_process': 7, 'ethics_policy': 7,
           'authorship_guidelines': 7, 'submission_deadlines': 6, 'withdrawal_policy': 6,
           'general_faq': 5})

Verified with:
- Both JSON files parse without error
- Intent and category distributions confirmed above (every category meets its minimum)

GitHub commit: not yet committed — data files created in working tree (uncommitted as of this session)

---

### Phase 1B — COMPLETE

What was built:
- backend/app/core/config.py — MODIFIED: added FAQ_CONFIDENCE_THRESHOLD (0.65),
  MAX_RETRIEVED_CHUNKS (3), DRAFTER_MAX_TOKENS (500) under a new "Pipeline tuning" group
- backend/.env.example — MODIFIED: added the three pipeline-tuning vars (new section)
- backend/pyproject.toml — MODIFIED: added python-dateutil>=2.9 (rank-bm25 + anthropic already present)
- backend/app/repositories/__init__.py — CREATED (empty package marker)
- backend/app/repositories/email_repository.py — CREATED: EmailRepository
  (create_email, get_email_by_id, get_emails_by_status, update_email_status,
   get_email_queue, count_emails_by_status)
- backend/app/repositories/policy_repository.py — CREATED: PolicyRepository
  (get_all_policies, get_policies_by_category, bulk_insert_policies)
- backend/app/repositories/audit_repository.py — CREATED: AuditRepository
  (log_action, get_audit_trail, get_recent_actions)

Key decisions:
- ORM class names confirmed from backend/app/db/models.py: `Email`, `AuditLog`,
  `PolicyDocument` (spec referred to them as EmailModel/AuditModel — these are
  type aliases only; the real names are used in code).
- Dependency manifest is pyproject.toml (NOT requirements.txt) — confirmed via Phase 0
  notes in this file. Only python-dateutil was missing; rank_bm25 and anthropic were
  already declared, so no duplicates were added.
- DB access uses async select() throughout; every write commits then refreshes; reads
  return None / [] on miss and never raise.
- Deviations from spec (and why):
  1. ANTHROPIC_API_KEY already existed as `str | None = None` (Phase 0). Did NOT add a
     duplicate `str = ""` field — a second field of the same name is a code smell and
     both defaults are falsy (the local-fallback check is unaffected). .env.example
     likewise already had ANTHROPIC_API_KEY= (intentionally blank for local fallback),
     so it was left as-is rather than set to "your_key_here".
  2. Email/AuditLog primary key is an INTEGER autoincrement, but the spec signatures
     type email_id as `str`. Kept the `str` signature (API contract) and coerce to int
     internally; a non-numeric id resolves to not-found (None/[]) instead of raising.
  3. update_email_status `metadata` has no dedicated column on Email. It is applied as
     updates to the JSON pipeline-output columns (classification/routing/draft) when
     those keys are present; other keys are ignored.
  4. get_email_queue filters lane via `json_extract(routing, '$.lane')` since the lane
     lives inside the routing JSON (RoutingDecision.lane), not a top-level column.
  5. bulk_insert_policies projects each dict onto real columns and accepts `id` as an
     alias for `policy_key`, so the project's policies.json loads directly; extra keys
     (source, tags) are dropped (no columns for them in the MVP schema).
  6. Mutable default `metadata: dict = {}` kept to match the spec interface exactly;
     it is only read, never mutated, so the usual pitfall does not apply.

Verified with:
- Import test (run from backend/ via .venv):
    python -c "from app.repositories.email_repository import EmailRepository; \
               from app.repositories.policy_repository import PolicyRepository; \
               from app.repositories.audit_repository import AuditRepository; \
               print('All repositories import cleanly')"
  Output: All repositories import cleanly
- Config load check: FAQ_CONFIDENCE_THRESHOLD=0.65, MAX_RETRIEVED_CHUNKS=3, DRAFTER_MAX_TOKENS=500
- In-memory async smoke test exercised all 12 methods (create/update/queue/lane-filter/
  counts/bulk-insert/audit trail/recent) — all passed, including not-found handling.

Tests added:
- None (tests arrive in Phase 1D)

GitHub commit: feat(backend): add repository layer and update config

---

## Current Status

| Phase | Status | What It Contains |
|---|---|---|
| Phase 0 | Complete | Skeleton, config, DB, frontend shell |
| Phase 1A | Complete | Toy dataset + knowledge base JSON |
| Phase 1B | Complete | Repository layer + config updates |
| Phase 1C | Next | Pipeline modules |
| Phase 1D | Pending | API routes + seed script + tests |
| Phase 2 | Future | Trainable classifier + RL router |
| Phase 3 | Future | Frontend UI build-out |
| Phase 4 | Future | Evaluation + research instrumentation |

---

## Session Update Instructions

At the end of EVERY session without exception, Claude must:
1. Append a new entry under Phase History following the format above
2. Update the Current Status table
3. Run: type CLAUDE.md to confirm the file saved correctly
4. Report: "CLAUDE.md updated — [phase name] logged"

This is not optional. If a session ends without this step, the project memory is broken.
