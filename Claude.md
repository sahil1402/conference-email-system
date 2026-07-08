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
| ROUTING_STRATEGY | Router (`rule_based`\|`rl`) | `rule_based` |
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
| emails | Email | Incoming email + lifecycle (status, classification/routing/draft JSON) |
| audit_logs | AuditLog | Append-only actions (actor, action, `timestamp`, `extra_metadata` [DB col "metadata"]) |
| policy_documents | PolicyDocument | FAQ/policy KB entries (policy_key, title, content, category, score) |

Migrations: `cd backend && alembic upgrade head` (env: `backend/migrations/`; initial `988d40d1a9ee_initial_schema.py`; checkpoint `507ef4c2d805_phase3c_postgres_ready`).

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

---

## Current Status — Phases 0–4 COMPLETE · Phase 5A–5I COMPLETE · 111/111 tests passing
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

## Open Blockers (active)
- **NCSA Delta GPU access pending** — the Phase 3D local drafter (MODEL_PROVIDER=local, Ollama on Delta) is implemented and unit-tested with mocks but NOT yet run on real GPU hardware; awaiting Delta access for live local-model validation.
- **Synthetic dataset** — data/emails/toy_dataset.json (30) and data/eval/ground_truth.json (58) are hand-written synthetic emails; all eval baseline numbers (58-email set: classification 82.8% / retrieval hit-rate 96.5% / routing 74.1%) are on synthetic data, not real conference email traffic.

## Phase 5 — In Progress
5A eval/observability ✅ done · 5B calibration ✅ done · 5C retrieval fusion ✅ done · 5D no-API drafter ✅ done · 5E live queue + calibration view ✅ done · 5F chair-edit diff + shortcuts ✅ done · 5G active learning flag ✅ done · 5H drafter adapter spec ✅ done · 5I Docker/CI ✅ done · 5J demo recording

## Session Update Instructions
At the end of EVERY session: (1) append/compress the phase entry under Phase History; (2) update the Current Status table; (3) run `type CLAUDE.md` to confirm the save; (4) report "CLAUDE.md updated — [phase] logged". Not optional — skipping it breaks project memory.
