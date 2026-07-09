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

## Tech Stack
| Layer | Technology |
|---|---|
| Frontend | Next.js 14 + TypeScript + Tailwind CSS v3 + shadcn/ui |
| Backend | Python + FastAPI + async SQLAlchemy |
| Database | SQLite via Alembic (PostgreSQL-ready, Phase 3C) |
| AI | Anthropic API or local model, swappable via config |
| Retrieval | BM25 (rank_bm25) or FAISS dense vectors, swappable via config |
| Testing | pytest + pytest-asyncio |

Dependencies via `backend/pyproject.toml` (no `requirements.txt`). Windows `.venv` uses plain `pip` (not `--break-system-packages`, which is Linux PEP-668 only).

## Architecture Rules (Non-Negotiable)
Six modules stay separate and independently replaceable:
1. Classifier (intent + confidence) · 2. Retriever (policy lookup) · 3. Router (faq vs human_review) · 4. Drafter (AI reply) · 5. Persistence (repositories only, never raw SQL in pipeline) · 6. UI (Next.js, never mixed with backend logic)

## Config Flags (`backend/app/core/config.py`)
Typed pydantic-settings `Settings`; env → `.env`. Access: `from app.core.config import settings` (cached via `get_settings()`). First four are the swappable module seams.
| Flag | Purpose | Default |
|---|---|---|
| MODEL_PROVIDER | Drafter provider (`anthropic_api`\|`anthropic`\|`local`\|`fallback`) | `anthropic_api` |
| CLASSIFIER_BACKEND | Classifier (`keyword`\|`trainable`) | `keyword` |
| RETRIEVAL_BACKEND | Retriever (`bm25`\|`faiss`) | `bm25` |
| ROUTING_STRATEGY | Router lane decision (`rule_based`\|`rl`) | `rule_based` |
| CHAIR_ROUTING_STRATEGY | Chair router — which chair (Phase 6A; `intent_mapping`) | `intent_mapping` |
| CONFIDENCE_THRESHOLD | Min classifier confidence for FAQ lane | `0.75` |
| FAQ_CONFIDENCE_THRESHOLD | Min confidence router applies for FAQ auto-reply | `0.65` |
| MAX_RETRIEVED_CHUNKS | Max policy chunks retrieved | `3` |
| DRAFTER_MAX_TOKENS | Max drafter tokens | `500` |
| DRAFT_MODEL | Drafter model id (never hardcode model names in source) | `claude-opus-4-8` |
| LOCAL_MODEL_BASE_URL | Local OpenAI-compatible endpoint | `http://localhost:11434/v1` |
| LOCAL_MODEL_NAME | Local model name | `llama3.1:8b` |
| FAISS_MODEL_NAME | Embedding model for FAISS | `all-MiniLM-L6-v2` |
| ANTHROPIC_API_KEY | Anthropic secret | `None` |
| DATABASE_URL | Async DB URL (normalized to aiosqlite at runtime) | `sqlite:///./conference_email.db` |
| SYNC_DATABASE_URL | Sync DB URL (Alembic / sync tooling) | `sqlite:///./conference_email.db` |

## Database Tables (`backend/app/db/models.py`)
Pipeline outputs (classification, routing, draft) stored as JSON columns on `emails`.
| Table | Model | Purpose |
|---|---|---|
| emails | Email | Incoming email + lifecycle (status, classification/routing/draft JSON; `assigned_chair_id` FK→chairs, Phase 6A) |
| audit_logs | AuditLog | Append-only actions (actor, action, `timestamp`, `extra_metadata` [DB col "metadata"]) |
| policy_documents | PolicyDocument | FAQ/policy KB entries (policy_key, title, content, category, score) |
| chairs | Chair | Conference chairs for assignment (name, role_title, `areas` JSON list, active); empty areas = fallback (Phase 6A) |

Migrations: `cd backend && alembic upgrade head` (env: `backend/migrations/`; initial `988d40d1a9ee_initial_schema.py`; checkpoint `507ef4c2d805_phase3c_postgres_ready`; Phase 6A `1f51f0224943_phase6a_chairs` — chairs table + emails.assigned_chair_id + 5 seeded chairs).

## Folder Structure
```
conference-email-system/{CLAUDE.md, README.md, LICENSE, *.pdf}
data/emails/toy_dataset.json (30) · data/knowledge_base/policies.json (45) · data/eval/ground_truth.json (40)
scripts/generate_progress_pdf.py
backend/  pyproject.toml · alembic.ini · main.py (FastAPI root, /health, /api/v1/health/model)
  migrations/{env.py, versions/} · scripts/{seed.py, run_eval.py} · reports/ · models/ (trained artifacts) · tests/
  app/core/config.py · app/db/{database.py, models.py} · app/models/{enums.py, schemas.py}
  app/repositories/{email,policy,audit}_repository.py
  app/pipeline/{classifier, retriever, faiss_retriever, router, rl_router, drafter, trainable_classifier, orchestrator}.py
  app/api/routes/{emails,dashboard,auto_replies,audit,training}.py · app/api/v1/{emails,analytics,retrieval}.py
frontend/  package.json (Next.js 14.2.35) · src/{app/, components/, lib/, hooks/, types/index.ts}
```

## Testing Policy
Every pipeline module has a test file. Tests run without real DB/API (mock both, or in-memory SQLite via StaticPool + ASGITransport). `cd backend && python -m pytest tests/ -v`

## Engineering Rules
Always: read existing code first; keep modules separate + typed; DB access via repositories; test every pipeline module; update this file at end of session.
Never: mix frontend/backend logic; hardcode model names in source; create monolithic files; skip the CLAUDE.md update.

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

### Phase 0 — Foundation — Complete
- Backend scaffold: main.py (FastAPI, CORS, /health), config.py (Settings + get_settings()), db/{database.py: async engine/session/Base, models.py}, models/{enums.py, schemas.py Pydantic v2}, pipeline + api/routes stubs; alembic.ini + migrations/988d40d1a9ee_initial_schema.py; pyproject.toml
- Frontend: Next.js 14 shell (src/app, components, lib, types)
- Verified: /health 200; alembic upgrade head clean

### Phase 1 — Data + Pipeline + API — Complete
- Data: data/emails/toy_dataset.json (30 labeled), data/knowledge_base/policies.json (45 chunks)
- Repositories (app/repositories/): EmailRepository/PolicyRepository/AuditRepository — async select() throughout, reads return None/[] never raise, str email_id coerced to int PK; DB access via repos only
- Pipeline (app/pipeline/ flat modules): classifier.py (IntentClassifier keyword-overlap, VALID_INTENTS + KEYWORD_RULES), retriever.py (PolicyRetriever BM25 over policies.json, lazy index), router.py (EmailRouter: sensitive-intent override + FAQ_CONFIDENCE_THRESHOLD/grounding gate), drafter.py (ResponseDrafter, reads DRAFT_MODEL, no-key/error fallbacks), orchestrator.py (EmailPipeline classify→retrieve→route→draft→persist+audit)
- API: app/api/v1/emails.py (ingest, queue, {id}, approve, reroute) + analytics.py (summary, recent-activity) under /api/v1; scripts/seed.py; config +FAQ_CONFIDENCE_THRESHOLD(0.65)/MAX_RETRIEVED_CHUNKS(3)/DRAFTER_MAX_TOKENS(500)/DRAFT_MODEL
- Status: 16 tests passing; seed 30/30 (lowercase "approved"/"rerouted"; lane in routing JSON)

### Phase 2 — Frontend — Complete (2026-06-27)
- API client + hooks: src/lib/api/ (axios client + interceptor; emails/analytics/audit), React Query hooks (useEmailQueue 15s, useAnalytics 30s, useEmailActions, useAudit)
- Design system: globals.css (dark, indigo #6366f1, var(--token) not shadcn theme); components/ui/ (Badge, ConfidenceBar, StatCard, etc.); layout/{Sidebar, AppShell responsive}
- Pages: /dashboard, /queue (split-pane review + approve/reroute), /auto-replies, /audit (timeline), /analytics (recharts); per-route layout.tsx metadata titles
- Note: frontend audit timeline initially read /api/v1/analytics/recent-activity (details empty) until real backend /audit shipped in Phase 3E
- Status: tsc clean; all routes 200; 16 tests passing

### Phase 3 — Production Hardening & Intelligence — Complete (2026-06-29)
Sub-phases (order 3E, 3C, 3D, 3A, 3B); each a config-flag swap, defaults unchanged:
- 3E Audit endpoint: audit_repository.py +get_audit_logs/get_audit_log_count/create_audit_log/get_audit_log_by_id; GET /api/v1/audit (paginated; filter email_id/action/actor) + /{log_id}; AuditLogResponse maps created_at←timestamp, details←extra_metadata; router remounted /api/v1; tests/test_audit_endpoint.py (5)
- 3C PostgreSQL-ready: +asyncpg 0.31.0 +psycopg2-binary 2.9.12; SYNC_DATABASE_URL in Settings; migrations/env.py already async, render_as_batch made SQLite-only; checkpoint 507ef4c2d805_phase3c_postgres_ready (zero drift); SQLite still default (switch = one .env line + alembic upgrade)
- 3D Local model: drafter.py provider-aware (_draft_anthropic/_draft_local httpx→{base}/chat/completions 60s/_fallback, never raises); MODEL_PROVIDER widened to anthropic_api/anthropic/local/fallback; +LOCAL_MODEL_BASE_URL/LOCAL_MODEL_NAME; GET /api/v1/health/model; httpx→main dep; tests/test_drafter_local.py (4)
- 3A Trainable classifier: app/pipeline/trainable_classifier.py (all-MiniLM-L6-v2 + LogisticRegression, joblib, singleton get_trainable_classifier, falls back to keyword_classify until trained); CLASSIFIER_BACKEND flag; POST /api/v1/train/classifier (app/api/routes/training.py, min 5→422, threadpool); artifacts backend/models/; ClassificationResult +method field; tests/test_trainable_classifier.py (5)
- 3B RL router: app/pipeline/rl_router.py (epsilon-greedy ε=0.15, arms auto_reply|human_review, per-intent {wins,trials}, optimistic 0.5; hard guards sensitive/conf<0.4/conf<threshold before bandit; state backend/models/rl_router_state.json; singleton get_rl_router); ROUTING_STRATEGY=rl (EmailRouter lazy-delegates); feedback in approve/reroute (_record_rl_feedback, best-effort); GET /api/v1/analytics/rl-stats; tests/test_rl_router.py (6)
- Status: 36/36 tests passing

### Phase 4A — FAISS Vector Retrieval — Complete
- app/pipeline/faiss_retriever.py (flat module — avoids Phase 1C subpackage-shadowing): FAISSRetriever, sentence-transformers all-MiniLM-L6-v2 + faiss-cpu 1.14.3, IndexFlatIP + normalize_L2 (cosine), lazy build, rebuild_index(); DB-backed via PolicyRepository (own async session); returns existing RetrievedChunk (true drop-in)
- get_retriever() factory in retriever.py (singleton, rebuilds on flag change; bm25→PolicyRetriever, faiss→FAISSRetriever, else ValueError); RETRIEVAL_BACKEND=bm25|faiss (BM25 default/unchanged); +FAISS_MODEL_NAME; orchestrator now calls get_retriever()
- GET /api/v1/retrieval/info (app/api/v1/retrieval.py); tests/test_faiss_retriever.py (6)
- Status: 42/42 tests passing

### Phase 4B — Eval Harness — Complete
- data/eval/ground_truth.json (40 emails, all 8 intents ×5, 15 faq / 25 human_review, 8 hard); scripts/run_eval.py (component-level: classifier+retriever+router, no DB writes; CLI --retrieval/--top-k/--output/--ground-truth/--verbose; JSON report → backend/reports/); tests/test_eval_harness.py (5); scikit-learn dep
- Metrics: per-intent precision/recall/F1, routing accuracy, retrieval hit-rate
- Baseline finding (BM25, top_k=3, keyword classifier, rule_based): classification 95.0% (macro F1 0.950), retrieval hit-rate 97.5%, routing 77.5% — human_review 25/25 but FAQ only 6/15 because the keyword classifier's confidence often falls below the 0.65 FAQ threshold, so genuine FAQ emails escalate to human_review (motivates threshold tuning + trainable classifier)
- Status: 47/47 tests passing

### Phase 4C — PDF Progress Document — Complete
- scripts/generate_progress_pdf.py (new; reportlab 5.0.0) → "Conference Email System Progress Report.pdf" at repo root (~4 pages), covers Phases 0–4; original "Conference Email System Design Document.pdf" untouched (40607 bytes)
- Content: Phase 3 (5 sub-phases, "In a meeting" callouts, summary table, 36 passing) + Phase 4 (4A/4B, green baseline callout, table, 47 passing, amber what-next); custom flowables (callout boxes + tables); drafter described generically ("configurable AI provider") per no-model-names-in-design-docs convention
- Status: generator exits 0; structural verification only (no PDF renderer available)

### Phase 5A — Eval & Observability Foundation — Complete
Three parts, all additive (no pipeline interface/return-type changes; defaults unchanged):
- **Per-email tracing**: app/core/tracing.py (new; stdlib logging + RotatingFileHandler, no new dep) — `PipelineTracer` buffers one JSON record per stage {timestamp, email_id, stage, input_summary, output_summary, duration_ms} and `flush(email_id)` writes them to backend/logs/pipeline_trace.jsonl (5MB×3) once the DB id exists (stages run pre-persist); `read_traces()`, `configure_tracing()` (test isolation). Wired into orchestrator.py via `with tracer.stage(...)` at each boundary (classifier/retriever/router/drafter) — drafter logs draft_length + provider, never the draft text. GET /api/v1/emails/{id}/trace (app/api/v1/emails.py; 404 on unknown email, else ordered trace). backend/logs/ gitignored. tests/test_tracing.py (6)
- **Retrieval-only metrics**: run_eval.py +recall@k/nDCG@k (k=1,3,5) — pure fns `recall_at_k`/`dcg_at_k`/`ndcg_at_k`/`score_retrieval` (single-gold; ideal DCG=1.0 so nDCG=DCG); `_evaluate_retrieval` runs BOTH bm25+faiss (query = body + GROUND-TRUTH intent, isolating retrieval from classifier). Report gains a distinct top-level `retrieval_metrics` section (never blended; existing metrics moved under `end_to_end_metrics` with `summary` kept as alias). CLI +`--retrieval-only`/`--no-retrieval-metrics`. ground_truth.json +`relevant_chunk_id` per entry (null when KB genuinely lacks a relevant policy → excluded + reported). tests/test_retrieval_metrics.py (5)
- **Expanded eval set**: ground_truth.json 40→58 (appended eval_041–058, 18 boundary cases engineered ambiguous near the 0.65 threshold via cross-intent cues; all 8 intents, 8 faq/10 human_review; existing 40 untouched — append-only diff)
- Findings: retrieval-only (56 scored) bm25 R@1/3/5 = 0.679/0.875/0.911, faiss = 0.839/0.982/1.000 → retrieval is NOT the FAQ bottleneck (even bm25 top-3 ≈ 0.88); FAQ escalation is classification/calibration, motivating 5B. Expanded baseline (BM25, keyword, rule_based): classification 82.8% (was 95.0% on 40), routing 74.1% (was 77.5%), FAQ 9/23 (was 6/15), retrieval hit-rate 96.5% (was 97.5%) — boundary cases hit classification, not retrieval.
- Status: 58/58 tests passing (47 + 6 tracing + 5 retrieval)

### Phase 5B — Classifier Calibration — Complete
Additive calibration layer between raw confidence and routing confidence; opt-in, off by default, no intent/threshold changes:
- **Module**: app/pipeline/calibration.py (new) — `ConfidenceCalibrator` (`fit(raw_scores, correct_labels)` / `calibrate(raw)->P(correct)`; methods `platt` [default, stable on small sets] + `isotonic`; joblib save/load; identity when unfitted, base-rate on degenerate labels); reliability metrics `brier_score`/`expected_calibration_error`/`reliability_table`; `get_calibrator(backend)` singleton (returns None when no artifact → graceful no-op) + `reset_calibrator_cache()`; fit-orchestration `collect_calibration_pairs`/`fit_calibrator_for_backend` (lazy imports break the classifier↔calibration cycle). Artifacts backend/models/calibration_{backend}.joblib
- **CLI**: scripts/fit_calibration.py (`--backend keyword|trainable`/`--method`) — runs classifier over ground_truth, fits+saves, prints before/after decile reliability tables (feeds 5E's diagram)
- **Wiring**: config +CALIBRATION_ENABLED (bool, default False). Applied in shared `IntentClassifier.classify` seam (both orchestrator + eval harness benefit): `confidence` left raw; ClassificationResult +`raw_confidence`/`calibrated_confidence` (None unless active). Router prefers calibrated when present (only the value it receives; threshold logic untouched). Missing artifact while enabled → warn once, fall back to raw, never raises. tracing.py classifier stage surfaces both confidences when active. POST /api/v1/train/calibration (mirrors /train/classifier, threadpool)
- **Key finding** (58-email set, BM25, keyword, rule_based; calibrator fit on same set → in-sample upper bound): calibration leaves classification unchanged (82.8%, by design) but routing 74.1%→**94.8%** and FAQ lane correct 9/23→**21/23**. Per-email diff: **12 genuine FAQs recovered** (human_review→faq, all correct — were escalating only because raw confidence < 0.65 gate), **0 over-promotions** (human_review lane preserved 34/35, no correct→wrong flips). The 2 FAQ emails still escalated (eval_057/058) are intent-misclassified as sensitive `review_assignment` → correctly held by the router's hard safety override, not a calibration failure. Reliability: ECE 0.160→0.036, Brier 0.180→0.139; Platt compresses confidence into ~0.78–0.84 (worth watching on real traffic). CALIBRATION_ENABLED left False in committed config — enable decision deferred to human review of these numbers.
- Status: 74/74 tests passing (58 + 16 calibration)

### Phase 5C — Retrieval Fusion — Complete
Third retrieval backend (Reciprocal Rank Fusion over BM25 + FAISS); genuine drop-in ablation, default unchanged:
- **Module**: app/pipeline/fusion_retriever.py (new flat module, faiss_retriever.py pattern) — `FusionRetriever` takes INJECTED bm25 (PolicyRetriever) + faiss (FAISSRetriever) instances (no duplicate embedder), pulls a candidate pool (default 10) from each, fuses by RRF `score = Σ 1/(k+rank)` with standard k=60, returns top-N `RetrievedChunk` (same contract). Ties break by policy_id (deterministic); richer metadata (tagged BM25 chunk) preferred on hydration; runs both rankers sequentially (tiny KB). +`document_count` property (delegates to bm25).
- **Wiring**: get_retriever() factory +`fusion` branch (lazy-imports + reuses one BM25 + one FAISS instance). config RETRIEVAL_BACKEND Literal → `bm25`|`faiss`|`fusion` (default still `bm25`, untouched). run_eval.py retrieval-only now evaluates all three; `/api/v1/retrieval/info` +fusion branch (reports rrf_k + dense model/index). tests/test_fusion_retriever.py (6, hand-computed RRF).
- **Fixed a latent bug**: run_eval `_evaluate_retrieval` mutated global RETRIEVAL_BACKEND + factory singleton per backend without restoring — previously leaked `faiss` (harmless), now would leak `fusion` and break `/retrieval/info`. Now restores original backend + resets singleton in a finally.
- **Key finding — ablation (58-set, query = body + ground-truth intent, 56 scored)**: fusion lands BETWEEN bm25 and faiss at every k and does NOT beat faiss alone. R@1/3/5 — bm25 0.679/0.875/0.911, **faiss 0.839/0.982/1.000**, fusion 0.768/0.946/0.982; nDCG@1/3/5 — bm25 0.679/0.798/0.812, **faiss 0.839/0.925/0.932**, fusion 0.768/0.876/0.890. Verdict: when one ranker dominates the other at every cutoff (5A showed faiss does), equal-weighted RRF only dilutes the leader — FAISS alone remains best. RETRIEVAL_BACKEND default left `bm25` (deployment decision, not this phase's).
- Status: 80/80 tests passing (74 + 6 fusion)

### Phase 5D — No-API Drafter Backend — Complete
Third drafter provider (zero model call) behind MODEL_PROVIDER; genuine drop-in, default unchanged:
- **Module**: app/pipeline/template_drafter.py (new flat module) — `TemplateDrafter.draft(email, intent, retrieved_chunks) -> DraftResponse` (same return type). Builds per-intent opening (one hand-written line per all 8 VALID_INTENTS) + retrieved policy chunk text **verbatim** (each tagged `[policy_id]`, citations set) + standard closing. Zero chunks → "routed to a program chair" message with `grounded: False` — never fabricates. No httpx / network / external dep → fully offline. DraftResponse imported from drafter.py; drafter imports TemplateDrafter lazily to break the cycle.
- **Wiring**: config MODEL_PROVIDER Literal +`template` (→ `anthropic_api`|`anthropic`|`local`|`template`|`fallback`; default still `anthropic_api`). drafter.py dispatch +`template` branch (`_draft_template`, mirrors Phase 3D local seam; skips prompt assembly, stamps lane into metadata). GET /api/v1/health/model reports template with status `healthy` (no external dep → trivially healthy, unlike anthropic/local which can be `unreachable`). .env.example MODEL_PROVIDER comment block documents all providers (feeds 5H adapter spec). tests/test_template_drafter.py (7).
- **Key finding — qualitative comparison** (6 FAQ-lane emails, 3 FAQ intents; anthropic side NOT run — no API key in this env, returns no-key stub): template quality is **fully bounded by retrieval quality** — it copies verbatim so hallucination risk is zero by construction, but it cannot synthesize, reorder, or drop an off-topic chunk. 5/6 samples solid (correct top chunk); eval_012 (virtual attendance) exposed the tradeoff — BM25's wrong top chunk (policy_031 authorship) was repeated verbatim, where an AI drafter would likely ignore it. Verdict: right as a **safety fallback** (AI-restricted venues / both other backends down) where guaranteed-grounded-but-rigid beats hallucination risk; not a general AI replacement; inherits every retrieval error with no filtering. MODEL_PROVIDER default left `anthropic_api`.
- Status: 87/87 tests passing (80 + 7 template drafter)

### Phase 5E — Live Queue + Calibration View — Complete
Two frontend-facing features (backend + Next.js), both additive; no pipeline/default changes:
- **Live queue via SSE**: app/core/events.py (new) — in-process `EventBroker` (per-connection asyncio queues; non-blocking best-effort `publish`, drops on full/slow consumer, no-op with no subscribers; no Redis/broker/new dep) + `get_event_broker()` singleton. audit_repository.py publishes `{email_id, action, actor, timestamp}` from its write path (`log_action` + `create_audit_log`) — the single seam every state change already flows through. GET /api/v1/emails/stream (StreamingResponse, text/event-stream; registered BEFORE /{email_id} so "stream" isn't parsed as an id; 15s heartbeat comment, disconnect-aware, deregisters on close). Frontend: useEmailQueueStream hook (EventSource → invalidates emailQueue + analytics React Query caches on each event; 15s poll in useEmailQueue kept as graceful fallback), LiveStatusDot (green=live / amber=reconnecting / gray=polling) in the queue header. tests/test_email_stream.py (4; stream-open test drives the handler directly since httpx ASGITransport buffers an infinite stream). Live HTTP round-trip verified (connect → ingest → event over the wire).
- **Calibration reliability diagram**: GET /api/v1/analytics/calibration — runs the active classifier over the eval set → per-decile rows {bucket, n, mean_confidence, accuracy, gap} for raw, plus calibrated when a fitted artifact exists (else calibrated_available:false / calibrated:null, no error) + Brier/ECE (raw & calibrated) + in-sample caveat string. Frontend: recharts ScatterChart on /analytics (mean_confidence x vs accuracy y, dashed y=x reference line, raw [amber] + calibrated [green] series, ZAxis sizes points by n, custom tooltip shows bucket/gap/n). In-sample caveat rendered as a VISIBLE amber callout under the chart (not a tooltip). useCalibration hook + getCalibration + CalibrationReport/CalibrationBucket types. tests/test_calibration_analytics.py (3, with/without artifact). Live endpoint confirms the 5B finding surfaces (ECE 0.160→0.036, Brier 0.180→0.139; 10 raw buckets, 2 calibrated after Platt compression).
- Status: 94/94 backend tests passing (87 + 4 SSE + 3 calibration analytics); frontend tsc clean

### Phase 5F — Chair-Edit Diff View + Keyboard Shortcuts — Complete
Two review-queue UX features (backend + Next.js), both additive; no schema migration, no default changes:
- **Chair-edit diff**: approve endpoint (app/api/v1/emails.py) now diff-aware — on approve with `final_text` differing from the current draft (whitespace-trimmed compare), it preserves the true original AI/template text in `draft.original_draft_text` (stable across repeat edits), sets `draft.draft_text`=edited + `is_edited`/`edited_by`, and writes an `approved` audit entry carrying BOTH full texts (`original_draft`+`edited_draft`, `edited:true`). Stored inside the existing `draft` JSON column (matches the JSON-column pattern → no migration). Approving unchanged/whitespace-only/no-final_text → `edited:false`, no diff, no draft mutation (identical ≠ an edit). tests/test_draft_diff.py (5). Frontend: lib/diff.ts (self-contained LCS word-level diff, no new dep), components/ui/DiffView.tsx (+DiffLegend; added=green, removed=red strikethrough); EmailDetail.tsx Show/Hide-changes toggle (original vs live edit, disabled until changed); audit page switched from /analytics/recent-activity → real /api/v1/audit (carries `details`), renders "edited before sending" tag + collapsed expandable diff for approved-with-edits entries.
- **Keyboard shortcuts**: EmailDetail keydown listener scoped to the review pane (only mounted when an email is open) — A=approve (same onApprove(editedDraft) as the button, human_review only), E=edit (focus draft textarea, cursor to end), R=reroute (opens the same inline form + focuses reason). Never fires when focus is in an INPUT/TEXTAREA/contentEditable or with Ctrl/Cmd/Alt (typing never hijacked); wired to the same handlers, no duplicate logic. Visible key-cap hint (A/E/R) by the action buttons. Frontend-only; manual browser verification noted.
- **5G hand-off**: the persisted diff (`details.edited` + both full texts on the `approved` audit entry) is exactly the signal Phase 5G active-learning flagging will consume to distinguish a meaningful chair rewrite from an as-is approval.
- Status: 99/99 backend tests passing (94 + 5 diff); frontend tsc clean

### Phase 5G — Active Learning Flagging — Complete
Auto-flags two DISTINCT candidate signals for a FUTURE human labeling pass — **no auto-retraining happens in this phase** (review list only). Additive; no default/threshold changes:
- **Module**: app/pipeline/active_learning.py (new flat module) — `should_flag_low_confidence(classification, threshold_margin=AL_CONFIDENCE_MARGIN)` flags the near-miss band `[FAQ_CONFIDENCE_THRESHOLD - margin, threshold)` = [0.50, 0.65); uses the Phase 5B seam via `_used_confidence` (prefers calibrated_confidence, else raw — the value the router actually compared). `should_flag_meaningful_edit(original, edited, min_change_ratio=AL_EDIT_RATIO)` uses `edit_change_ratio` = `1 - difflib.SequenceMatcher.ratio()` over word tokens (5F's diff is TS-only → reimplemented in Python, stdlib, no dep); typo fix stays below 0.15, rewrite exceeds it. `build_flag_events` returns the two as SEPARATE (action, details) tuples, never conflated. Config +AL_CONFIDENCE_MARGIN/AL_EDIT_RATIO (both 0.15, not hardcoded).
- **Wiring**: emails.py `_record_flag_events` (best-effort, never breaks the chair action) called from approve (both signals) + reroute (low-confidence only — no draft edit). Each fired signal → its own audit entry with a DISTINCT action type (`flagged_low_confidence` / `flagged_meaningful_edit`) — reuses the audit_logs table, no parallel logging. GET /api/v1/analytics/active-learning-candidates aggregates both actions per email → {candidates:[{email_id, subject, reason: low_confidence|meaningful_edit|both, low_confidence:{…}, meaningful_edit:{…}, flagged_at}], total}.
- **Frontend**: "Active-Learning Candidates" card on /analytics (below the calibration diagram — same research audience, avoids a new route); per candidate shows subject/#id, reason badge(s) with the number (confidence < threshold, % changed), and a "View in queue" link; clear empty state; caption reiterates review-list-only. useActiveLearningCandidates hook + getActiveLearningCandidates + AL types. (Honest limit: queue selection is client state, so the link goes to /queue, not a per-email deep link.)
- Status: 111/111 backend tests passing (99 + 12 active learning); frontend tsc clean

### Phase 5H — Drafter Adapter Spec — Complete
**Documentation-only — no code changed, no test-count change** (test-suite re-run intentionally skipped; still 111/111). New file: `docs/DRAFTER_ADAPTER_SPEC.md`.
- Formal, standalone spec of the drafter swap seam, written for a collaborator to plug in a fourth backend without reading the existing three. **Zero model/vendor names** (hard requirement — grep-verified against anthropic/claude/opus/sonnet/haiku/llama/ollama/openai/gpt/mistral/gemini/etc.; even the cloud key and provider aliases are described by role, not name).
- Sections: Purpose · Interface contract (public `async draft(email, classification, retrieved_chunks, routing) -> DraftResponse`; input/output types referenced by name in their real pipeline-module locations, NOT models/schemas.py) · Required behavior (never raise → fallback; never fabricate ungrounded claims; bounded time — cites the 60 s self-hosted generation timeout + 3 s health-probe timeout; health via /health/model per-provider branch) · Registration (4 edits: MODEL_PROVIDER Literal, drafter dispatch branch, .env.example block, main.py health branch) · Reference-implementations table (three backends by role only) · Known inconsistencies · Testing expectations (patterned on test_template_drafter.py).
- **Known inconsistencies documented, not papered over**: template backend's internal signature differs `(email, intent, chunks)` w/ adapter bridge; sync vs async branches; `generation_metadata["provider"]` absent on cloud success path; only self-hosted sets an explicit generation timeout; `model_used` semantics vary; citations derived differently; no formal ABC/Protocol (behavioral contract).
- Status: unchanged — 111/111 backend tests, frontend tsc clean (no code touched this phase)

### Phase 5I — Docker Compose + CI — Complete
One-command local spin-up + secret-free CI on every push/PR. No pipeline code changed (111/111 unchanged):
- **Docker**: backend/Dockerfile (python:3.11-slim; `pip install -e .`; CMD `alembic upgrade head && uvicorn main:app`). Built with **context = repo root** so the image mirrors the repo layout (/app/backend + /app/data) — the pipeline resolves the dataset from <root>/data. frontend/Dockerfile (node:18-alpine; npm install → build → `next start -H 0.0.0.0`; NEXT_PUBLIC_API_URL baked at build time as a build arg). docker-compose.yml (backend :8000 + named volume `backend-db` for the SQLite file via DATABASE_URL/SYNC_DATABASE_URL → ./dbdata; optional `env_file backend/.env required:false`; frontend :3000 depends_on backend; **no Postgres service** — SQLite default, with a comment on how to add Postgres later). .dockerignore (root, backend context) + frontend/.dockerignore — exclude caches/venv/node_modules/DBs/logs/pdfs, KEEP data/ + backend/models (5 KB).
- **NEXT_PUBLIC_API_URL deviation (intentional)**: set to `http://localhost:8000/api/v1`, NOT the `backend` service name as the phase brief suggested. NEXT_PUBLIC_* is inlined into the CLIENT bundle and all API calls are browser-side (every page is "use client" + React Query), so it must be host-reachable; the service name would break the running app. Verified working; reasoning documented in Dockerfile + compose comments.
- **Live verification** (`docker compose up --build`, run for real after starting the daemon): both images built, both containers Up; **backend GET /health → 200** (`{"status":"ok",...}`); **frontend / → 307 redirect → /dashboard → 200** (served `<title>ConfMail — Conference Email System</title>`). Torch/sentence-transformers install in the backend image works (heavy but no secret).
- **CI**: .github/workflows/ci.yml on push/PR to main — Job 1 backend (setup py3.11, `pip install -e ".[dev]"`, `alembic upgrade head`, `pytest tests/ -v`); Job 2 eval (`needs: backend`, `run_eval.py --no-retrieval-metrics` → uploads reports/ci_eval_report.json as an artifact); Job 3 frontend (node 18, `npm install`, `npx tsc --noEmit`). **All three run secret-free** (default keyword+BM25+no-key-fallback path; --no-retrieval-metrics avoids the embedding-model download); explicit comment forbids adding an external-provider-key job. Zero model/vendor names in any Docker/CI file (grep-verified).
- Status: unchanged — 111/111 backend tests, frontend tsc clean (no pipeline code touched this phase)

### Phase 6A — Multi-Chair Routing — Complete
Second routing decision ("which chair") added as a separate, swappable component beside the lane decision. Zero model names (hard constraint). Built in confirmed steps.
- **Step 1 — DB migration (done)**: new `Chair` model/table (id, name, role_title, `areas` JSON list, `active` bool, timestamps) + `Email.assigned_chair_id` (nullable FK → chairs.id, **ON DELETE SET NULL**, indexed). Migration `1f51f0224943_phase6a_chairs` (revises 507ef4c2d805); FK given an explicit name (`fk_emails_assigned_chair_id_chairs`) — SQLite batch mode rejects unnamed constraints. **Seeds 5 chairs in `upgrade()`** (reference data, so every env is routable after `alembic upgrade head`): Program Chair `[submission_deadline, formatting_requirements, submission_withdrawal, review_assignment, technical_issue]`; Diversity & Ethics Chair `[ethics_concern, authorship_dispute]`; Local Arrangements Chair `[general_inquiry]`; Publicity/Sponsorship Chair `[sponsorship, publicity, media_inquiry]`; General Chair `[]` (**empty areas = catch-all fallback**). Downgrade→upgrade round-trip verified clean.
- **Step 1.5 — classifier taxonomy expansion (done, this session)**: classifier now supports **11 intents** (was 8). Added `sponsorship`, `publicity`, `media_inquiry` to `VALID_INTENTS` + matching `KEYWORD_RULES` in `app/pipeline/classifier.py` (distinctive domain/multi-word cues; bare "press"/"media" deliberately avoided — would substring-match "Pressure" etc.). This closes a coverage gap: **the Publicity/Sponsorship Chair now has a genuine auto-routing path** (previously no classifier intent mapped to it → reachable only by manual reroute). New intents are neither FAQ-eligible nor sensitive, so the router escalates them to human_review (correct for chair assignment). Also added 3 matching openings to `template_drafter._OPENINGS` (test asserts `set(_OPENINGS)==set(VALID_INTENTS)`). Eval set 58→**67** (9 new ops-intent emails in ground_truth.json, 3 per new intent, `relevant_chunk_id: null` — KB has no policy for these). **The 11 intents**: submission_deadline, formatting_requirements, general_inquiry, review_assignment, authorship_dispute, submission_withdrawal, ethics_concern, technical_issue, sponsorship, publicity, media_inquiry.
- **Eval before/after (keyword, BM25, rule_based)**: original 8 intents — **zero regression** (per-email diff: intent, lane, AND confidence identical on all 58 original emails; per-intent F1 unchanged). New 3 intents — **9/9 classify correctly, all route to human_review** (per-intent F1 1.000 each). Overall classification 82.8%→**85.1%** (macro F1 0.825→0.873), routing 74.1%→**77.6%**. End-to-end retrieval hit-rate 96.5%→83.6% — **not a regression**: the 9 new emails have no relevant KB policy (null chunk, empty keywords) so they can't register a hit and mechanically dilute the denominator; retrieval-only ranking metrics (which exclude null-chunk emails) are unaffected.
- Note: legacy `app/models/enums.py::EmailIntent` + `schemas.py` use a different, unused vocabulary (FAQ_DEADLINE…) and are NOT in the active classification path — left untouched. Trainable classifier learns labels from data — no code change needed.
- **Step 2 — chair_router.py + config + wiring + unit tests (done)**: new module **`app/chair_router.py`** (top-level app/, per spec — a peer to the lane router, not under pipeline/): `ChairRoutingStrategy` ABC + `IntentMappingStrategy` (intent→`areas` lookup; deterministic tie-break by lowest id; empty-areas chair = catch-all fallback; `chair_id=None` when no owner AND no active fallback — never guesses) + `ChairInfo`/`ChairAssignment` pydantic models + `get_chair_router(strategy)` factory (raises on unknown). Pure & DB-free (strategy takes a `list[ChairInfo]`), mirroring the other pipeline modules. New config flag **`CHAIR_ROUTING_STRATEGY`** (`Literal["intent_mapping"]`, default `intent_mapping`; Literal is the swap seam — add `learned`/`rl` later without changing callers). New **`ChairRepository`** (get_all/get_active/get_by_id; no-exception contract). **Wired into `orchestrator.py`**: `_assign_chair` runs ONLY when lane==human_review (best-effort, never raises; empty roster → unassigned), sets `Email.assigned_chair_id`, and writes a `chair_assigned` audit entry capturing chair_id/name + **intent + confidence at assignment time** (the reroute-comparison signal for Step 3). Kept OUT of the tracer (trace contract stays exactly classify→retrieve→route→draft). `assigned_chair_id` added to `_email_to_dict`. tests/test_chair_router.py (12: correct match, new-ops-intents→Publicity Chair, no-match fallback, no-fallback→unassigned, inactive-owner→fallback, inactive-fallback→unassigned, no-active-chairs, tie-breaks, factory/flag). Zero model names (grep-verified). End-to-end smoke-verified against a seeded in-memory roster: ethics→Diversity&Ethics Chair, deadline→Program Chair, correct `chair_assigned` audit.
- **Step 3 — reroute path + toy dataset + integration tests (done)**: chair-reassignment endpoint **PATCH `/api/v1/emails/{id}/reassign-chair`** (`{reassigned_by, new_chair_id, reason}`; 404 on unknown email or chair; can target an inactive chair — deliberate human override). New `EmailRepository.assign_chair` (updates `assigned_chair_id` WITHOUT a lifecycle-status change — a reassignment keeps the email in human_review). **Reroute audit uses the EXISTING mechanism** (`audit_repo.log_action` → `audit_logs` table, no new table): a `chair_reassigned` entry captures **original_chair_id, new_chair_id, timestamp (AuditLog.timestamp), and intent+confidence at assignment time** (read off the stored classification) — confirmed sufficient as the future training signal. Toy dataset **`data/emails/toy_multichair.json`** (17 emails, JSON at data/emails/ per existing pattern) covering all chairs: Program (withdrawal/review/technical/deadline), Diversity & Ethics (ethics ×2/authorship), Local Arrangements (general_inquiry ×3), Publicity/Sponsorship (sponsorship ×2/publicity/media_inquiry), + 3 ambiguous (borderline-within-a-chair + a low-signal message). tests/test_chair_routing_integration.py (6, in-memory DB + seeded 5-chair roster: dataset→expected-chair assertion, all-five-chairs coverage incl. General Chair via inactive-owner fallback, faq-lane→no-chair, reassign updates+audit shape, 2×404). Zero model names (grep-verified).
- **Finding — general_inquiry split**: high-confidence general inquiries (mc_008–010, conf 0.95 + grounded) route to the **faq lane and get NO chair** (auto-replied); only genuinely-unclear ones (mc_017, conf 0.30) escalate to the Local Arrangements Chair. So under the fully-active roster the General Chair (empty areas) is a structural safety-net reached on an unowned intent or a deactivated owner — not by everyday traffic — which is the honest behavior given the intent taxonomy fully covers the four content chairs.
- Status: **Phase 6A COMPLETE — 129/129 backend tests passing** (111 + 12 chair-router unit + 6 chair-routing integration).

### Phase 6B — Multi-Chair Routing Frontend — Complete
UI for multi-chair routing (Next.js). Zero model names (grep constraint). Built in 5 confirmed steps; `npx tsc --noEmit` clean at each, full `npm run build` passes at the end.
- **Authorized backend exception (one endpoint)**: 6A shipped no way to *list* chairs, so — with explicit user sign-off (the phase was otherwise UI-only) — added a minimal read-only **`GET /api/v1/chairs`** (`app/api/v1/chairs.py`, `?active_only`; uses existing `ChairRepository`; registered in main.py; **no pipeline code touched**). This is the ONLY backend change in 6B. Everything else derives from existing endpoints.
- **Step 1 — types + API client**: `Chair` + `ReassignmentEvent` types, `Email.assigned_chair_id`, `ReassignChairRequest`; synced `IntentLabel` 8→11. Client: `getChairs()`, `reassignChair()` (matches PATCH contract exactly), `getReassignmentEvents()` (derived from `/audit?action=chair_reassigned`). Hooks: `useChairs` (roster + `byId` map), `useReassignChair` (mutation, invalidates queue/analytics), `useReassignmentEvents`.
- **Step 2 — queue badge + filter**: `ChairBadge` (per-chair color via `chairColor()` palette in format.ts — deterministic by id, reused across badge/chart; muted "Unassigned" pill). Badge on each human_review card in `EmailListItem`; assigned-chair dropdown (All / Unassigned / each chair) in `EmailFilters` + queue filter logic.
- **Step 3 — reassign UI**: extended (not replaced) `EmailDetail` — current-chair badge in header, "Reassign chair" button → inline picker (all chairs; inactive marked `(inactive)`, current `· current`) + optional reason. Optimistic local update + query invalidation; inline success confirmation (no toast infra in codebase → deliberately not introduced); `ErrorBanner` on failure. Added `C` keyboard shortcut (A/E/C/R hint row).
- **Step 4 — routing rationale panel**: "Routing Rationale" collapsible (human_review only) reconstructing the intent_mapping rule client-side — 3 honest cases: **area match** (matched area chip highlighted), **fallback** (empty-areas chair, stated explicitly — never implies a false match), **manual reassignment** (chair owns neither the intent nor is fallback → could only come from a human).
- **Step 5 — analytics**: two per-chair charts on /analytics — **Email Volume per Chair** (current ownership from the queue, all chairs incl. idle) and **Reassignments by Chair** (count of emails moved *away from* each chair, from the reassignment audit feed) — plainly labeled as a preview of where the rule-based router is overridden, explicitly NOT a trained-model metric. Chart bars use the same per-chair colors. Empty/loading/error states reuse the existing `EmptyState`/`ErrorBanner`/`Panel`/`ChartEmpty` patterns.
- Status: **Phase 6B COMPLETE — frontend `npm run build` passes** (all 10 routes, lint + types clean). Backend unchanged except the one authorized read-only endpoint. **Next: full 6A+6B manual browser testing** (spin up backend + frontend, exercise assign/reassign/rationale/analytics end-to-end) before Phase 6 is considered fully done.

### Phase 6C — Paginated-aggregate bug class (queue-derived stats) — Complete
Recurring bug class found during demo prep: **frontend surfaces computing a count/chart/aggregate client-side from the default-paginated `/queue` fetch (`limit=20`, newest-first)** instead of a backend aggregate — so any row outside the newest 20 was silently dropped from stats/charts/filtered lists. Swept every consumer of `useEmailQueue()`; fixed all four remaining instances (three fixed just prior: Auto-Replies count, Analytics chair-distribution, queue chair filter). **Fix pattern, every time:** a dedicated backend aggregate field, OR a full/scoped server-side query — never a default-paginated list repurposed for counting.
- **Backend aggregates on `analytics_summary`** (all over ALL emails/audit rows; JSON-friendly string keys): `chair_distribution`, `confidence_distribution` (6-band histogram, shared bucket logic), `faq_avg_confidence`, `reassignment_by_chair` (grouped over `chair_reassigned` audit rows via new `AuditRepository.count_reassignments_by_original_chair`; `"unassigned"` bucket for null original chair). Plus `EmailRepository.count_by_chair`.
- **`/queue` filters fully server-side** via shared `EmailRepository._queue_conditions`: params `lane`, `chair_id`, `unassigned`, `status`, `search` (subject/sender, case-insensitive `ilike`), with a filter-scoped `total` from `count_email_queue`. Queue page builds all filters into one server query (debounced search 250ms); count badge = `total`, list = `emails` (limit 200 covers the queue), "showing N of total" note if ever truncated.
- **Frontend**: all three Analytics charts (confidence histogram, chair volume, reassignments) read `summary.*` — the analytics page no longer fetches `/queue` at all. Auto-Replies avg reads `summary.faq_avg_confidence`. Queue list + badge are server-driven.
- **Verified NOT a bug**: Dashboard stat cards read `summary`; its "Recent Emails" is a newest-5 preview (`emails.slice(0,5)`) — a list preview, correct with a newest-first fetch, not an aggregate.
- **Regression tests** (data seeded OUTSIDE the newest-20 / beyond the 200-row audit cap, proving before-bug/after-fix): `test_analytics_aggregates.py` (5), `test_queue_status_search_filter.py` (5), atop the earlier `test_queue_lane_filter.py` (3) + `test_chair_surface_counts.py` (4). Zero model names; `chair_router.py` / `orchestrator.py` / migrations untouched. **All known instances of this bug class are resolved.**

---

## Current Status — Phases 0–4 COMPLETE · Phase 5A–5I COMPLETE · Phase 6A + 6B COMPLETE · Phase 6C COMPLETE · 146/146 backend tests passing · frontend build clean
| Phase | Status | Summary |
|---|---|---|
| Phase 0 | Complete | Skeleton, config, DB, frontend shell |
| Phase 1 | Complete | Data (30 emails / 45 policies), repositories, pipeline, v1 API, seed — 16 tests |
| Phase 2 | Complete | Full Next.js frontend (dashboard/queue/auto-replies/audit/analytics) |
| Phase 3 | Complete | 3E audit · 3C postgres-ready · 3D local model · 3A trainable classifier · 3B RL router — 36 tests |
| Phase 4 | Complete | 4A FAISS retrieval · 4B eval harness · 4C progress PDF — 47/47 tests |
| Phase 5A | Complete | Per-email tracing · retrieval-only metrics (recall@k/nDCG@k, bm25 vs faiss) · eval set 40→58 boundary cases — 58/58 tests |
| Phase 5B | Complete | Confidence calibration (platt/isotonic, opt-in flag, off by default) · routing 74.1%→94.8% & FAQ 9/23→21/23 when enabled, 0 over-promotions — 74/74 tests |
| Phase 5C | Complete | RRF fusion retriever (bm25+faiss, k=60) · three-way ablation: fusion between bm25 & faiss, does NOT beat faiss alone · default still bm25 — 80/80 tests |
| Phase 5D | Complete | Template drafter (MODEL_PROVIDER=template, zero model call, verbatim-grounded) · fully bounded by retrieval quality, safest fallback · default still anthropic_api — 87/87 tests |
| Phase 5E | Complete | Live queue via SSE (in-process EventBroker, /emails/stream, connection dot) · calibration reliability diagram on /analytics (raw vs calibrated, visible in-sample caveat) — 94/94 tests, tsc clean |
| Phase 5F | Complete | Chair-edit diff (original preserved in draft JSON + both texts in audit, word-level diff in queue & audit page) · A/E/R keyboard shortcuts (scoped, typing-safe) — feeds 5G — 99/99 tests, tsc clean |
| Phase 5G | Complete | Active-learning flagging (near-miss confidence + meaningful edit, two separate signals) · distinct audit actions + candidates endpoint + /analytics card · flags for future labeling only, no auto-retrain — 111/111 tests, tsc clean |
| Phase 5H | Complete | Drafter adapter spec (docs/DRAFTER_ADAPTER_SPEC.md) — documentation-only, zero model names (grep-verified), documents the swap-seam contract + known inconsistencies for a 4th backend — no code/test change |
| Phase 5I | Complete | Docker Compose (backend+frontend, SQLite volume, one-command spin-up — live-verified /health 200 + /dashboard 200) · secret-free GitHub Actions CI (tests · eval artifact · tsc) — 111/111 unchanged |
| Phase 6A | Complete | Multi-chair routing ("which chair", separate from the lane decision). Chair table + Email.assigned_chair_id FK (5 chairs seeded in migration) · classifier 8→**11 intents** (Publicity/Sponsorship auto-routable) · `app/chair_router.py` (ChairRoutingStrategy ABC + IntentMappingStrategy + `CHAIR_ROUTING_STRATEGY` flag + ChairRepository) wired into orchestrator (human_review only, best-effort, `chair_assigned` audit) · PATCH `/emails/{id}/reassign-chair` + `chair_reassigned` audit (original/new chair + intent/confidence, existing mechanism, no new table) · toy_multichair.json (17) + integration/reroute tests — 129/129 |
| Phase 6B | Complete | Multi-chair frontend. One authorized read-only backend endpoint (`GET /api/v1/chairs`); rest UI-only. Chair badge (per-chair color) + assigned-chair filter on the queue · reassign-chair picker in the detail pane (optimistic + inline confirm/error, `C` shortcut) · routing rationale panel (match/fallback/manual, no false match) · 2 analytics charts (volume-per-chair + reassignments-away-from-chair, labeled as router-override preview, not a model metric) · `npm run build` clean |
| Phase 6C | Complete | Fixed a **bug class**: frontend counts/charts/aggregates computed client-side from the capped `/queue` page (dropped out-of-window rows). Backend aggregates on `analytics_summary` (`chair_distribution` · `confidence_distribution` · `faq_avg_confidence` · `reassignment_by_chair`) · `/queue` full server-side filtering (lane/chair/unassigned/status/search) + filter-scoped `total` · analytics page no longer fetches `/queue`. Regression tests seeded outside the page/200-audit-cap window. All known instances resolved · `chair_router`/`orchestrator`/migrations untouched · zero model names — **146/146 tests, build clean** |

## Open Blockers (active)
- **NCSA Delta GPU access pending** — the Phase 3D local drafter (MODEL_PROVIDER=local, Ollama on Delta) is implemented and unit-tested with mocks but NOT yet run on real GPU hardware; awaiting Delta access for live local-model validation.
- **Synthetic dataset** — data/emails/toy_dataset.json (30) and data/eval/ground_truth.json (67, was 58 pre-6A) are hand-written synthetic emails; all eval numbers (67-email set: classification 85.1% / routing 77.6%; original-8 subset unchanged from the 58-set 82.8%/74.1%) are on synthetic data, not real conference email traffic.

## Phase 5 — In Progress
5A eval/observability ✅ done · 5B calibration ✅ done · 5C retrieval fusion ✅ done · 5D no-API drafter ✅ done · 5E live queue + calibration view ✅ done · 5F chair-edit diff + shortcuts ✅ done · 5G active learning flag ✅ done · 5H drafter adapter spec ✅ done · 5I Docker/CI ✅ done · 5J demo recording

## Session Update Instructions
At the end of EVERY session: (1) append/compress the phase entry under Phase History; (2) update the Current Status table; (3) run `type CLAUDE.md` to confirm the save; (4) report "CLAUDE.md updated — [phase] logged". Not optional — skipping it breaks project memory.
