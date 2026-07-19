# CLAUDE.md — Conference Email System
# Project memory. Read fully at the start of every session before writing any code.

## Project Overview
AI-powered conference email management for AAAI/NeurIPS/ICML/ICLR. Two-lane workflow:
- FAQ Lane: high-confidence emails auto-replied using retrieved policy text only
- Human Review Lane: ambiguous/sensitive emails get an AI draft + chair approval

## Collaborators
- Sahil — lead developer · Prof. Yan — PI / stakeholder
- Jiacheng — collaborator (constraint: no specific model names in any design documents)

## Project Path
`D:\USC\The Melady Labs\conference-email-system`

## Branch / State Note (2026-07-17)
`main` now includes Jiacheng's Phase 7 work (fast-forward merge, HEAD `c4ed3f5`).
**Not on main yet** (implemented on `feature/production-hosting-v2`, cut from
`main`, pending review/merge): the SQLite→PostgreSQL migration, the Docker
Postgres `db` service, and full `SYNC_DATABASE_URL` removal. The `external_api`
drafter is **deliberately excluded** on v2 (the `local` OpenAI-compatible
provider covers that use case with zero new code). `main` today is still
**SQLite-only**, drafter Literal has **no `external_api`**, and
`SYNC_DATABASE_URL` is still present — do not mark these done for `main` until
`feature/production-hosting-v2` is merged.

## Tech Stack
| Layer | Technology |
|---|---|
| Frontend | Next.js 14 + TypeScript + Tailwind CSS v3 + shadcn/ui |
| Backend | Python + FastAPI + async SQLAlchemy |
| Database | SQLite via Alembic (PostgreSQL-ready since Phase 3C; not migrated on main) |
| AI | Cloud API, self-hosted OpenAI-compatible endpoint, template, or fallback — swappable via config |
| Retrieval | BM25 (rank_bm25), FAISS dense vectors, or RRF fusion — swappable via config |
| Testing | pytest + pytest-asyncio (`ml` marker for embedding-heavy tests) |

Dependencies via `backend/pyproject.toml` (no `requirements.txt`). Windows `.venv` uses plain `pip` (not `--break-system-packages`, which is Linux PEP-668 only).

## Architecture Rules (Non-Negotiable)
Six modules stay separate and independently replaceable:
1. Classifier (intent + confidence) · 2. Retriever (policy lookup) · 3. Router (faq vs human_review) · 4. Drafter (AI reply) · 5. Persistence (repositories only, never raw SQL in pipeline) · 6. UI (Next.js, never mixed with backend logic)

## Config Flags (`backend/app/core/config.py`)
Typed pydantic-settings `Settings`; env → `.env`. Access: `from app.core.config import settings` (cached via `get_settings()`). The first flags are the swappable module seams.
| Flag | Purpose | Default |
|---|---|---|
| MODEL_PROVIDER | Drafter provider (`anthropic_api`\|`anthropic`\|`local`\|`template`\|`fallback`) | `anthropic_api` |
| CLASSIFIER_BACKEND | Classifier (`keyword`\|`trainable`) | `keyword` |
| RETRIEVAL_BACKEND | Retriever (`bm25`\|`faiss`\|`fusion`) | `bm25` |
| ROUTING_STRATEGY | Router lane decision (`rule_based`\|`rl`) | `rule_based` |
| CHAIR_ROUTING_STRATEGY | Chair router — which chair (`intent_mapping`) | `intent_mapping` |
| QUERY_STRATEGY | Retrieval-query build (`prefix`=legacy body+intent · `distill`=one LLM call → 1-3 queries + intent) | `prefix` |
| CALIBRATION_ENABLED | Apply confidence calibration layer | `False` |
| ALLOW_AUTO_SEND | Transport gate — `True` lets complete FAQ drafts release without approval | `False` |
| CONFIDENCE_THRESHOLD | Min classifier confidence for FAQ lane | `0.75` |
| FAQ_CONFIDENCE_THRESHOLD | Min confidence router applies for FAQ auto-reply | `0.65` |
| MAX_RETRIEVED_CHUNKS | Max policy chunks retrieved | `3` |
| AL_CONFIDENCE_MARGIN / AL_EDIT_RATIO | Active-learning flag thresholds | `0.15` / `0.15` |
| DRAFTER_MAX_TOKENS | Max drafter tokens | `500` |
| DRAFT_MODEL | Drafter model id (never hardcode in source; read from here) | `claude-sonnet-5` |
| LOCAL_MODEL_BASE_URL / LOCAL_MODEL_NAME | Self-hosted OpenAI-compatible endpoint + model | `http://localhost:11434/v1` / `llama3.1:8b` |
| LOCAL_MODEL_API_KEY | Optional bearer token for a hosted keyed chat-completions endpoint | `None` |
| STYLE_GUIDE_PATH | Reply style guide appended to the drafter system prompt (backend/-relative) | `../data/style_guide/style_guide_v2.md` |
| FAISS_MODEL_NAME | Embedding model for FAISS | `all-MiniLM-L6-v2` |
| ANTHROPIC_API_KEY | Cloud API secret | `None` |
| DATABASE_URL | Async DB URL (SQLite→aiosqlite; Postgres+asyncpg passthrough) | `sqlite:///./conference_email.db` |
| SYNC_DATABASE_URL | Sync URL for Alembic / sync tooling only | `sqlite:///./conference_email.db` |

## Database Tables (`backend/app/db/models.py`)
Pipeline outputs (classification, routing, draft) stored as JSON columns on `emails`.
| Table | Model | Purpose |
|---|---|---|
| emails | Email | Incoming email/ticket + lifecycle (status, classification/routing/draft JSON; `assigned_chair_id` FK→chairs; Zendesk fields `source`, `zendesk_ticket_id` [unique], `zendesk_requester_id`, `zendesk_status`, `zendesk_created_at`, `zendesk_updated_at`, `last_processed_comment_id`) |
| email_thread_messages | EmailThreadMessage | Child rows per Zendesk comment (Piece 3): `email_id` FK→emails (CASCADE), `zendesk_comment_id` [unique], `public` bool, `author_id`, `author_role`, `plain_body`, `html_body`, `created_at` [thread order], `via_channel`, `ingested_at`. Initial inquiry = first `public` end-user message by `created_at` (derived by query) |
| audit_logs | AuditLog | Append-only actions (actor, action, `timestamp`, `extra_metadata` [DB col "metadata"]) |
| policy_documents | PolicyDocument | Policy KB entries (policy_key, title, content, category, score, `tags` JSON + `source`) |
| chairs | Chair | Conference chairs for assignment (name, role_title, `areas` JSON, active); empty areas = fallback |

Migrations (`cd backend && alembic upgrade head`; env `backend/migrations/`):
`988d40d1a9ee_initial` → `507ef4c2d805_phase3c_postgres_ready` → `1f51f0224943_phase6a_chairs` (chairs + emails.assigned_chair_id + 5 seeded chairs) → `b8d3f6a1c204_phase_e_policy_tags_source` (policy tags + source) → `f1a2b3c4d5e6_phase_f_policy_kb_layers` → `a7b8c9d0e1f2_phase_f_policy_audit` → `d2e4f6a8b0c1_zendesk_ticket_schema` (Zendesk fields on emails + email_thread_messages; **head**, applied to demo Postgres 2026-07-19).

## Folder Structure
```
conference-email-system/{CLAUDE.md, README.md, LICENSE, *.pdf}
data/emails/toy_dataset.json (30) · data/knowledge_base/policies.json (93 real AAAI-27 chunks) · data/eval/ground_truth.json
data/policy_corpus_real/*.md (6 source docs) · data/style_guide/{style_guide_v1.md, style_guide_v2.md, manifest.json}
archive/ (56-chunk corpus track, superseded by 7C) · scripts/generate_progress_pdf.py
backend/  pyproject.toml · alembic.ini · main.py (FastAPI root, /health, /api/v1/health/model)
  migrations/{env.py, versions/} · scripts/ (seed, run_eval, chunk_policies, bench_real, distiller/eval tooling) · reports/ · models/ · tests/ (conftest hermetic)
  app/core/{config.py, tracing.py, events.py, send_gate.py} · app/db/{database.py, models.py} · app/models/{enums.py, schemas.py}
  app/repositories/{email,policy,audit,chair}_repository.py
  app/pipeline/{classifier, retriever, faiss_retriever, fusion_retriever, router, rl_router, chair_router, drafter, template_drafter, trainable_classifier, calibration, active_learning, distiller, orchestrator}.py
  app/api/routes/{emails,dashboard,auto_replies,audit,training}.py · app/api/v1/{emails,analytics,retrieval}.py
frontend/  package.json (Next.js 14.2.35) · src/{app/, components/, lib/, hooks/, types/index.ts}
docs/{PIPELINE_AUDIT.md, ZENDESK_API.md, DRAFTER_ADAPTER_SPEC.md, exp_tracking/E001-E003}
```

## Testing Policy
Every pipeline module has a test file. Tests run without real DB/API (mock both, or in-memory SQLite via StaticPool + ASGITransport). A hermetic autouse conftest fixture forces `MODEL_PROVIDER=fallback` / no key / `QUERY_STRATEGY=prefix` so the suite never hits a hosted model. Fast iteration: `-m "not ml"` (162) skips embedding-heavy tests; full gate = both halves (184). `cd backend && python -m pytest tests/ -v`

## Engineering Rules
Always: read existing code first; keep modules separate + typed; DB access via repositories; test every pipeline module; update this file at end of session.
Never: mix frontend/backend logic; hardcode model names in source (use `DRAFT_MODEL`/`LOCAL_MODEL_NAME`); create monolithic files; skip the CLAUDE.md update.
DB note: `main` defaults to a local SQLite file (created under `backend/` at the process CWD). Seed/migrate via `cd backend && python scripts/...`.

## How to Run
```
cd backend && pip install -e . && alembic upgrade head && uvicorn main:app --reload
cd frontend && npm install && npm run dev
cd backend && python scripts/seed.py            # seed DB
cd backend && python -m pytest tests/ -v         # tests
cd backend && python scripts/run_eval.py         # eval harness
python scripts/generate_progress_pdf.py          # progress PDF
```

---

## Phase History

### Phases 0–4 — Foundation → Intelligence — Complete
- **0 Foundation**: FastAPI scaffold (main.py, config.py Settings+get_settings(), db async engine/session/Base + models, Pydantic v2 schemas), Alembic initial migration, Next.js 14 shell. /health 200.
- **1 Data+Pipeline+API**: 30 labeled toy emails; repositories (Email/Policy/Audit, async, reads never raise); flat pipeline modules (classifier keyword-overlap, retriever BM25, router sensitive-override+FAQ gate, drafter provider-aware w/ fallbacks, orchestrator classify→retrieve→route→draft→persist+audit); v1 API (ingest/queue/{id}/approve/reroute + analytics); seed.py.
- **2 Frontend**: API client + React Query hooks; dark indigo design system; pages /dashboard /queue (split-pane review) /auto-replies /audit /analytics (recharts).
- **3 Hardening** (config-flag swaps, defaults unchanged): 3E audit endpoint · 3C PostgreSQL-ready (asyncpg/psycopg2 deps, checkpoint migration; SQLite still default) · 3D local drafter (`_draft_local`, /health/model) · 3A trainable classifier (MiniLM+LogReg, `CLASSIFIER_BACKEND`) · 3B RL router (epsilon-greedy bandit, `ROUTING_STRATEGY=rl`).
- **4**: 4A FAISS retrieval (`get_retriever()` factory, `RETRIEVAL_BACKEND=faiss`) · 4B eval harness (per-intent P/R/F1, routing acc, retrieval hit-rate) · 4C progress PDF.

### Phase 5 — Eval, Observability, Fusion, Drafters, Review UX — Complete
- **5A**: per-email tracing (`app/core/tracing.py` → logs/pipeline_trace.jsonl, `/emails/{id}/trace`) · retrieval-only metrics (recall@k/nDCG@k) · eval set boundary cases.
- **5B**: confidence calibration (`calibration.py`, platt/isotonic, `CALIBRATION_ENABLED` opt-in, off by default; big routing win when on, 0 over-promotions).
- **5C**: RRF fusion retriever (`fusion_retriever.py`, bm25+faiss, k=60, `RETRIEVAL_BACKEND=fusion`) — on the toy corpus fusion sat between bm25 and faiss (default stayed bm25).
- **5D**: template drafter (`template_drafter.py`, `MODEL_PROVIDER=template`, zero model call, verbatim-grounded) — safest fallback.
- **5E**: live queue via SSE (`events.py` EventBroker, `/emails/stream`) · calibration reliability diagram on /analytics.
- **5F**: chair-edit diff (original preserved in draft JSON + both texts in audit, word-level diff) · A/E/R keyboard shortcuts.
- **5G**: active-learning flagging (`active_learning.py`, near-miss confidence + meaningful edit → two distinct audit actions + candidates endpoint + /analytics card; review-list only, no auto-retrain).
- **5H**: drafter adapter spec (`docs/DRAFTER_ADAPTER_SPEC.md`, zero model names).
- **5I**: Docker Compose (backend+frontend, **SQLite volume**) + secret-free GitHub Actions CI (tests · eval artifact · tsc). Live-verified /health 200 + /dashboard 200.

### Phase 6 — Multi-Chair Routing — Complete
- **6A**: second routing decision ("which chair"), separate from the lane router. Chair table + `Email.assigned_chair_id` FK (5 seeded chairs). Classifier taxonomy **8→11 intents** (+sponsorship, publicity, media_inquiry). `app/pipeline/chair_router.py` (ChairRoutingStrategy ABC + IntentMappingStrategy + `CHAIR_ROUTING_STRATEGY`) wired into orchestrator (human_review only, best-effort, `chair_assigned` audit). PATCH `/emails/{id}/reassign-chair` + `chair_reassigned` audit.
- **6B**: multi-chair frontend (`GET /api/v1/chairs` the only backend add). Chair badge + assigned-chair filter on queue · reassign picker (optimistic, `C` shortcut) · routing-rationale panel · 2 analytics charts.
- **6C**: fixed a **bug class** — surfaces computing counts/aggregates client-side from the capped `/queue` page dropped out-of-window rows. Backend aggregates on `analytics_summary` (chair_distribution · confidence_distribution · faq_avg_confidence · reassignment_by_chair) · `/queue` full server-side filtering + filter-scoped total. Regression tests seeded outside the page window.

### Real-Corpus + Phase 7 — Real AAAI-27 Corpus, Distiller, Placeholder Contract, Send Gate — Complete
Jiacheng's track, now on `main`. Zero model names in code/docs (data quotes AAAI's own policy verbatim).
- **Real corpus (7C unification)**: `data/knowledge_base/policies.json` is the canonical **93-chunk** real AAAI-27 corpus (`policy_101`–`193`, subsection-level, contextual titles) chunked from 6 official markdown docs (`scripts/chunk_policies.py`). The earlier 56-chunk track (`policy_046`–`101`) is superseded and moved to `archive/`. Migration `b8d3f6a1c204` added `policy_documents.tags`/`source` (FAISS↔BM25 tag parity); both indexes rebuild clean on the 93 chunks. Retriever-only; classifier/router unchanged.
- **Query distillation (E003)**: `app/pipeline/distiller.py` — one LLM call rewrites the email into 1-3 compact policy-vocabulary retrieval queries **and** classifies intent (`method="llm_distiller"`), gated by `QUERY_STRATEGY=distill` (default `prefix` = legacy bit-for-bit). On any failure → keyword classifier + subject+body[:600] query. Real-ticket ablation: distilled-joined hit@3 **.892 vs .649**. Deploy recipe: `QUERY_STRATEGY=distill` + `RETRIEVAL_BACKEND=fusion`.
- **Placeholder reply contract (7F)**: drafter emits structured REPLY/CITATIONS/NOTES FOR CHAIR; chair-facing gaps become inline `[CHAIR: …]` placeholders + `notes_for_chair` (never in the reply body). Deterministic enforcement: orchestrator forces human_review when placeholders exist; approve endpoint 409s while `[CHAIR: …]` remains; leak detector flags residual meta language (flag, never rewrite). Internal `policy_NNN` ids scrubbed from requester text. Real-ticket leak rate **86% → 0%**.
- **Send gate**: `app/core/send_gate.py` `authorize_send()` + `POST /emails/{id}/send` — single precondition for any future transport. Default (`ALLOW_AUTO_SEND=False`) only status "approved" is sendable regardless of lane; unresolved placeholders block even an approved email; both outcomes audited; no transport yet → authorized send returns 501, draft stays queued.
- **Style guide**: distilled from real chair replies (v1) + curated **v2** (adopted after blinded A/B). Appended to the drafter system prompt (contract in the fixed prompt, voice in the guide) via `STYLE_GUIDE_PATH` — see today's entry.
- **Test infra**: hermetic autouse conftest fixture (fallback provider / no key / prefix) cut the suite from ~7.5 min to seconds; `ml` marker on embedding-heavy modules. Zendesk groundwork (`docs/ZENDESK_API.md`, read-only OAuth + pull script) — no poller/write-back yet.
- Findings live in `docs/PIPELINE_AUDIT.md` + `docs/exp_tracking/E001-E003`. Coverage on real traffic ~18.3% (human-review lane is the product); classifier real-intent accuracy 57.8% (fine for chair routing, not as an FAQ gate).

### Today (2026-07-17) — style_guide_v2 made the committed default — Complete
- `STYLE_GUIDE_PATH` default `None` → `../data/style_guide/style_guide_v2.md` (config.py + .env.example), commit **`c4ed3f5`**. Uses the backend/-relative `../data` form so it resolves from the app CWD (Docker WORKDIR /app/backend, local `cd backend`, pytest rootdir backend/) — a bare `data/...` would silently no-op. Drafter loader / v1 / manifest untouched. Verified: default loads the real 2789-char v2 guide into the system prompt; **184/184 tests pass**.
- **Minor future cleanup (flagged, not done)**: `test_no_style_guide_by_default` in `test_drafter_local.py` is now a mild misnomer — it monkeypatches `STYLE_GUIDE_PATH=None` (testing the explicit-None override), which is still valid and passing, but the default is no longer None. Rename later (keep the intent), low priority.

### 2026-07-17 (later) — SQLite→PostgreSQL migration — Complete on `feature/production-hosting-v2` (branch; NOT merged to main)
Infra + data-only; the six pipeline modules untouched (`chair_router`/`orchestrator`/`seed.py`/migration files unchanged). `external_api` drafter **deliberately excluded** (`MODEL_PROVIDER=local` at an OpenAI-compatible endpoint already covers it — zero new code).
- **Docker Postgres**: `db` service `postgres:16-alpine` in `docker-compose.yml` — named volume `postgres-data`, `pg_isready` healthcheck, port bound **127.0.0.1:5432 only** (never 0.0.0.0). Backend `DATABASE_URL` built from the **same `${POSTGRES_*}`** values as `db` (single source of truth), asyncpg driver, `depends_on: db {condition: service_healthy}`. Dropped the now-dead SQLite `backend-db` volume/mount.
- **`SYNC_DATABASE_URL` removed entirely** (config.py, docker-compose.yml, .env.example, stale Dockerfile comment) — grep-confirmed read nowhere; Alembic reads the async `DATABASE_URL` via `migrations/env.py`. config.py `DATABASE_URL` default kept SQLite (safe test/local/CI default; Postgres injected via compose env).
- **Postgres-compat fix (`func.json_extract` → dialect-agnostic accessor)**: both call sites — `email_repository._queue_conditions` → `Email.routing["lane"].as_string()`; `audit_repository.count_reassignments_by_original_chair` → `AuditLog.extra_metadata["original_chair_id"].as_integer()`. `func.json_extract` is SQLite-only (`UndefinedFunctionError` on Postgres). Repo-wide grep confirmed exactly these two.
- **Migrations on Postgres**: `alembic upgrade head` clean through `988d40d1a9ee → 507ef4c2d805 → 1f51f0224943 → b8d3f6a1c204`; PG schema **byte-identical** to a fresh-migrated SQLite (incl. `policy_documents.tags`/`source`, `audit_logs.metadata`). Reseeded via `seed.py`.
- **Tests (+8, 184→192, all green)**: `tests/test_postgres_migration.py` (skipif unless `TEST_DATABASE_URL` is a pg DSN) — driver/dialect resolution, schema assertion, CRUD, and **two json_extract regression tests** (fail if either call site reverts). Fixture: schema provisioned once per module **synchronously via psycopg2 (no event loop)**; async engine/session **per test** (avoids cross-loop asyncpg "another operation is in progress"). `tests/test_env_example_config.py` — every `.env.example` `MODEL_PROVIDER` is a valid config Literal (guards the `external_api`-not-in-Literal bug). CI backend job gains a **secret-free `postgres:16-alpine` service** + `TEST_DATABASE_URL`.
- **Verified**: alembic head on PG · PG-vs-SQLite schema diff identical · 3-way (raw psql · `async_session_factory` · live HTTP `/queue?lane=` + `/analytics/summary` reassignment aggregate) exercising both fixed queries · full suite **192 passed / 0 failed** (6 PG tests: 6-pass-with / 6-skip-without `TEST_DATABASE_URL`).
- **Deviations / flags**: (1) PG test suite gated on `TEST_DATABASE_URL` **only** — dropped the `DATABASE_URL` fallback so the `drop_all`/`create_all` suite can never target a real/dev DB. (2) `scripts/generate_progress_pdf.py` still carries historical `SYNC_DATABASE_URL` narrative (Phase-3C record, out of scope). (3) ⚠️ `backend/.env` (gitignored) holds a live-looking OpenAI key (`sk-proj-…`) under `LOCAL_MODEL_API_KEY` — recommend rotation.
- **Demo data (volume state only, not repo)**: this branch's Postgres volume reset to the full **47-email** demo set — 30 `toy_dataset.json` via `seed.py` + 17 `toy_multichair.json` via the live `/ingest` pipeline (real `local` drafter). Citations draw from the real 93-chunk corpus (`policy_101`–`192`); per-chair Program 26 / D&E 8 / Local Arr 8 / Pub-Spon 4 / General **0** (expected — general_inquiry FAQ-lane + low-signal → Local Arr).
- **Proposed commit (NOT committed)**: `feat(db): migrate SQLite→PostgreSQL — Docker Postgres service, single-source DATABASE_URL, drop SYNC_DATABASE_URL, dialect-agnostic JSON accessors, PG test suite + CI Postgres service`

### 2026-07-19 — Zendesk integration Pieces 1–3 (credential provider · scope test · ticket schema) — Complete, migration APPLIED to demo Postgres
Multi-piece Zendesk integration. No pipeline module touched (classifier/retriever/router/drafter/orchestrator unchanged); `pull_zendesk_tickets.py` untouched.
- **Piece 1 — OAuth credential provider**: `app/integrations/zendesk/credential_provider.py` — `ZendeskCredentialProvider` ABC + `TokenCredentialProvider` (HTTP Basic) + `OAuthCredentialProvider` (client_credentials; proactive refresh, named `TOKEN_LIFETIME_SLACK_SECONDS=1500` vs 1800s expiry) + `get_zendesk_credential_provider()` factory keyed on `ZENDESK_AUTH_MODE`. Typed errors: `ZendeskCredentialError` (config, fail-loud naming missing fields), `ZendeskAuthError` (token-endpoint failure). Config additions: `ZENDESK_AUTH_MODE`/`SUBDOMAIN`/`EMAIL`/`API_TOKEN`/`OAUTH_CLIENT_ID`/`OAUTH_CLIENT_SECRET`/`OAUTH_SCOPE`. Secret read from Settings/.env, never `docs/secrets.txt`. **Note: this foundation's source had been lost (only orphaned `.pyc` in `__pycache__`, never committed) — reconstructed faithfully from the bytecode, then extended.** 12 tests. `.env`/.env.example populated (oauth mode, client `confmail`).
- **Piece 2 — write-scope diagnostic**: `scripts/zendesk_scope_test.py` (one-off, `--confirm-write`-gated). **Result: full write access confirmed** — `read write` token granted (HTTP 200), internal-note write succeeded (HTTP 200) on ticket 22009 (a `solved` ticket, chosen over `closed` since closed tickets are immutable and would confound the scope signal). Test note left on 22009 for manual cleanup.
- **Piece 3 — ticket data model**: reused `Email` as the parent (a Zendesk ticket maps 1:1 → Email row; avoids forking the domain / touching the pipeline) + new `email_thread_messages` child table. Enums `EmailSource`, `MessageAuthorRole`. Migration `d2e4f6a8b0c1` (batch_alter for SQLite parity; `source` NOT NULL `server_default='toy_dataset'`; unique `zendesk_ticket_id`/`zendesk_comment_id`; FK CASCADE). Initial inquiry derived by query (first `public` end-user message by `created_at`), no denormalized flag. 9 tests incl. Alembic up→down→up round-trip on a temp SQLite file.
- **Migration APPLIED to the demo Postgres** via `docker compose exec backend alembic upgrade head` (backend image rebuilt first — no source volume mount, so the container had to be rebuilt to see the new code; startup also auto-runs `upgrade head`). **Verified**: DB `a7b8c9d0e1f2 → d2e4f6a8b0c1`; **47 demo emails untouched, all `source='toy_dataset'`** (0 NULL); `email_thread_messages` exists (11 cols) and empty.
- **Tests**: local full suite **207 passed / 6 skipped / 0 failed** (authoritative — SQLite). In-container run showed 6 environmental failures (NOT regressions): 4× `test_tracing` = documented Postgres cross-event-loop asyncpg contamination (pass in isolation, 6/6); 2× `test_env_example_config` = `FileNotFoundError` because `.dockerignore` excludes `**/.env.*` so `.env.example` isn't shipped in the image. New schema tests pass 9/9 in-container.
- **Flags**: (1) ephemeral `pip install pytest` done inside the running container for the in-container test run — lost on next recreate, harmless. (2) test note on real ticket 22009 pending manual deletion. (3) `.env` still `ZENDESK_OAUTH_SCOPE=read` — Piece 5 send endpoint will need `read write`.

---

## Current Status — Phases 0–6C COMPLETE · Real-Corpus + Phase 7 COMPLETE (on main) · main 184/184 · `feature/production-hosting-v2` 192/192 · frontend build clean
| Phase | Status | Summary |
|---|---|---|
| 0–2 | Complete | Scaffold/config/DB/frontend shell · data+pipeline+v1 API · full Next.js frontend |
| 3 | Complete | audit endpoint · postgres-ready · local drafter · trainable classifier · RL router |
| 4 | Complete | FAISS retrieval · eval harness · progress PDF |
| 5 | Complete | tracing · calibration · fusion · template drafter · SSE queue+calibration view · chair-edit diff+shortcuts · active-learning flag · adapter spec · Docker(SQLite)+CI |
| 6A/6B/6C | Complete | multi-chair routing (11 intents, chair_router, reassign) · frontend · paginated-aggregate bug-class fix |
| Real-Corpus + 7 | Complete | 93-chunk real AAAI-27 corpus (56-chunk archived) · query distiller (`QUERY_STRATEGY`) · placeholder reply contract · send gate (`ALLOW_AUTO_SEND`) · style guide v2 · hermetic conftest · Zendesk groundwork |
| Today | Complete | style_guide_v2 committed default (`c4ed3f5`) |
| PG migration (v2) | Complete on branch (unmerged) | SQLite→Postgres on `feature/production-hosting-v2`: Docker `db` service (loopback, healthcheck) · single-source `DATABASE_URL` · `SYNC_DATABASE_URL` removed · dialect-agnostic JSON (`json_extract` fix, both sites) · PG test suite + CI Postgres · 192/192 · `external_api` excluded by design |
| Zendesk Pieces 1–3 | Complete · migration applied | OAuth credential provider (`ZENDESK_AUTH_MODE`) · write-scope confirmed (ticket 22009) · ticket data model (`Email` extended + `email_thread_messages`, migration `d2e4f6a8b0c1` applied to demo Postgres, 47 emails intact) · local 207/207 · no pipeline module touched |

## Open Blockers (active)
- **Postgres / Docker-Postgres implemented on `feature/production-hosting-v2` — NOT merged** — SQLite→Postgres migration, Docker `db` service, single-source `DATABASE_URL`, `SYNC_DATABASE_URL` removal, `func.json_extract` fix, PG test suite + CI Postgres service all done on the branch (192/192); awaiting review/merge to `main`. `external_api` drafter **deliberately excluded** (the `local` OpenAI-compatible provider covers it). Until merged, `main` stays SQLite-only with `SYNC_DATABASE_URL` present and no `external_api` in the Literal.
- **NCSA Delta GPU access pending** — the self-hosted (`MODEL_PROVIDER=local`) drafter is implemented + mock-tested but not run on real GPU hardware.
- **Synthetic email dataset** — the policy corpus is real (93 chunks) but `data/emails/toy_dataset.json` and `data/eval/ground_truth.json` remain hand-written synthetic; eval numbers are on synthetic traffic. Real-ticket eval (Phase 7) uses gitignored PII data under `data/eval_real/`.
- **Zendesk fetch/write-back missing** — credential layer (OAuth read+write, verified), write-scope diagnostic, and ticket **schema** now exist (Pieces 1–3; `zendesk_ticket_id` + `email_thread_messages` live on demo Postgres). Still missing: the API adapter/poller (Piece 4) and the real send endpoint driving `PUT /tickets/{id}` (Piece 5; send gate contract is live, transport is not).

## Session Update Instructions
At the end of EVERY session: (1) append/compress the phase entry under Phase History; (2) update the Current Status table; (3) run `type CLAUDE.md` to confirm the save; (4) report "CLAUDE.md updated — [phase] logged". Not optional — skipping it breaks project memory.

## Codebase Navigation
Before exploring unfamiliar code or checking cross-module calls, use codebase-memory-mcp's 
search_graph / trace_path / get_architecture tools instead of grep/read-by-file. 
Especially verify module boundaries (classifier/retriever/router/drafter/persistence/UI) 
via trace_path before changes that could cross them.