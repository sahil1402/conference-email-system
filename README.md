# ConfMail — Automated Conference Email Reply & Routing System

> An AI-powered email management platform for academic conference organizations. Built for the Melady Lab at USC, targeting venues like AAAI, NeurIPS, ICML, and ICLR.

![Status](https://img.shields.io/badge/status-Phase%200--5%20complete%20%7C%20Phase%206A%20in%20progress-brightgreen)
![Tests](https://img.shields.io/badge/tests-111%2F111%20passing-brightgreen)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Next.js](https://img.shields.io/badge/Next.js-14-black)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Overview

Conference program chairs receive hundreds of emails per cycle — submission deadline questions, formatting queries, visa letter requests, review conflicts, and appeals. Most are repetitive and answerable from public policy documents. A small fraction require genuine human judgment.

ConfMail separates these two classes automatically.

**FAQ Lane** — High-confidence, policy-grounded emails are answered automatically. No hallucinated policies. Every response is traced to a source document.

**Human Review Lane** — Novel, ambiguous, or sensitive emails are routed to a chair queue with an AI-generated draft. Chairs can approve, edit, or reroute with a full audit trail. As of Phase 6, this lane branches further: instead of landing in one generic queue, each email is assigned to the specific chair responsible for that area (Program, Diversity & Ethics, Local Arrangements, Publicity/Sponsorship, or General as fallback) — with reroutes between chairs captured as a future training signal.

The system is designed as a research platform: every component (classifier, retriever, router, chair assignment, drafter, database) is modular and config-flag-swappable, with reinforcement-learning-based routing and local-only deployment already implemented for conferences with external API restrictions.

---

## Architecture

```
Inbound Email
      │
      ▼
┌─────────────┐
│  Classifier │  ── Intent classification with confidence score
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Retriever  │  ── FAQ knowledge base search
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Router    │  ── Confidence-threshold or bandit-based lane decision
└──────┬──────┘
       │
   ┌───┴───┐
   │       │
   ▼       ▼
 FAQ    Human
 Lane   Review
   │       │
   │       ▼
   │  ┌───────────────┐
   │  │ Chair Router  │  ── Which chair owns this email (intent-to-area mapping)
   │  └───────┬───────┘
   │          │
   │          ▼
   │  ┌──────────┐
   │  │  Drafter │  ── AI draft generation with policy citations
   │  └──────────┘
   │          │
   ▼          ▼
Auto-    Chair-Specific
Reply    Approval Queue
```

### Pipeline Stages

Every stage is a swappable, config-flag-controlled module — none of these are phase-gated placeholders; all backends listed are live for each stage unless marked planned.

| Stage | Backend Options | Notes |
|---|---|---|
| Classifier | `keyword` · `trainable` | Trainable backend uses sentence-transformers embeddings + LogisticRegression, exposed via `/api/v1/train/classifier` |
| Retriever | `bm25` · `faiss` · `fusion` | Grounds replies in the real **AAAI-27 policy corpus** (56 chunks parsed from 6 official AAAI policy documents — call for papers, submission instructions, code of conduct, publication ethics, publication policies, and a cross-reference guide — each tagged from a fixed 25-term taxonomy). `bm25` is lexical; `faiss` uses sentence-transformers (`all-MiniLM-L6-v2`) with `IndexFlatIP` cosine similarity; `fusion` is reciprocal-rank fusion over both. Both `bm25` (from the corpus file) and `faiss` (from the DB) now surface chunk tags — tag parity across backends |
| Router (lane) | `threshold` · `rl` | RL backend is an online, epsilon-greedy contextual bandit updated on every approve/reroute |
| Chair Router | `intent_mapping` · `learned` (planned) | Assigns human-review emails to a specific chair by matching classified intent against each chair's owned areas; falls back to a general/catch-all chair on no match. Reroutes are logged as the future training signal for a learned assignment policy |
| Drafter | `anthropic_api` · `local` · `template` | `local` targets an Ollama-compatible endpoint (pending GPU compute); `template` is a zero-dependency, zero-API fallback |
| Database | `sqlite` · `postgresql` | SQLite for MVP, PostgreSQL migration-ready via asyncpg + Alembic |

---

## Tech Stack

### Backend
- **Python 3.11+** with **FastAPI** — async REST API
- **SQLAlchemy (async)** + **SQLite / PostgreSQL** — persistence layer, migration-ready
- **Alembic** — database migrations
- **Pydantic v2** — schema validation and serialization
- **rank-bm25** — lexical FAQ retrieval
- **faiss-cpu** + **sentence-transformers** (`all-MiniLM-L6-v2`) — dense vector retrieval
- **scikit-learn** — trainable classifier (LogisticRegression), Platt-scaling calibration, and eval metrics
- **anthropic_api** backend — classification and draft generation (with `local`/Ollama-compatible and `template` fallbacks)
- **pytest** + **pytest-asyncio** — 111/111 tests passing

### Frontend
- **Next.js 14** (App Router) with **TypeScript**
- **Tailwind CSS v3** + **shadcn/ui** — component library
- **recharts** — analytics visualizations
- **React Query** + **axios** — data fetching layer
- **lucide-react** — icons

### Infrastructure
- Monorepo structure (`backend/` + `frontend/`)
- Environment-driven configuration via `.env`
- Alembic migrations for schema evolution
- Docker Compose — one-command spin-up (live-verified)
- GitHub Actions CI — three-job, secret-free pipeline
- **ReportLab** — auto-generated project progress PDF (`scripts/generate_progress_pdf.py`)

---

## Project Structure

```
conference-email-system/
├── backend/
│   ├── main.py                        # FastAPI entry point
│   ├── pyproject.toml
│   ├── .env.example
│   ├── app/
│   │   ├── core/
│   │   │   └── config.py              # Swappable backend flags
│   │   ├── api/
│   │   │   └── routes/
│   │   │       ├── emails.py          # Email ingestion + retrieval
│   │   │       ├── pipeline.py        # Classification + routing
│   │   │       ├── drafts.py          # Draft approval workflow
│   │   │       ├── analytics.py       # Dashboard metrics
│   │   │       ├── audit.py           # Paginated/filterable audit log
│   │   │       └── train.py           # POST /api/v1/train/classifier
│   │   ├── pipeline/
│   │   │   ├── classifier/            # keyword + trainable classifiers
│   │   │   ├── retriever/             # BM25 + FAISS retrievers (flat module)
│   │   │   ├── router/                # threshold + RL bandit lane router
│   │   │   ├── chair_router.py        # NEW (Phase 6A): intent-to-chair assignment strategy
│   │   │   └── drafter/               # anthropic_api + local + template draft generation
│   │   ├── models/
│   │   │   ├── enums.py               # EmailIntent, RoutingLane, EmailStatus
│   │   │   └── schemas.py             # Pydantic v2 contracts
│   │   └── db/
│   │       ├── database.py            # Async SQLAlchemy setup
│   │       ├── models.py              # ORM models — Email, AuditLog, PolicyDocument, Chair (new)
│   │       └── repositories/          # Data access layer
│   ├── data/
│   │   ├── toy_emails.json            # Labeled toy emails across all intents
│   │   ├── toy_emails_multichair.py   # NEW (Phase 6A): toy dataset exercising all 5 chairs
│   │   └── policies.json              # Real AAAI-27 policy corpus — 56 tagged chunks (policy_046-101)
│   ├── data/eval/
│   │   └── ground_truth.json          # Eval set covering all classifier intents
│   ├── models/                        # Trained classifier artifacts
│   ├── migrations/                    # Alembic migrations
│   └── scripts/
│       ├── seed.py                    # DB seeding script
│       ├── run_eval.py                # Eval CLI (sklearn metrics + JSON report)
│       └── generate_progress_pdf.py   # Living project progress PDF generator
└── frontend/
    └── src/
        ├── app/
        │   ├── layout.tsx             # Root layout with sidebar
        │   ├── dashboard/page.tsx
        │   ├── queue/page.tsx         # Split-pane email review queue
        │   ├── auto-replies/page.tsx
        │   ├── analytics/page.tsx     # recharts-based analytics + calibration reliability diagram
        │   └── audit/page.tsx         # Timeline audit view
        ├── components/
        │   ├── layout/                # Sidebar, Header, PageWrapper
        │   ├── email/                 # EmailCard, StatusBadge, ConfidenceBar
        │   ├── pipeline/              # Classification, Retrieval, Routing panels
        │   └── dashboard/             # StatsCard, Charts, ActivityFeed
        ├── hooks/                     # queue, analytics, audit, actions hooks
        ├── lib/
        │   ├── api.ts                 # Typed API client
        │   └── utils.ts
        └── types/
            └── index.ts               # TypeScript types mirroring backend schemas
```

> Chair-assignment UI (assigned-chair badges, filter-by-chair, reroute-to-chair dropdown, routing-rationale panel) is planned for Phase 6B, once the backend chair router is verified.

---

## Configuration

All backend behavior is controlled by config flags in `backend/.env`:

```env
# Classifier backend: "keyword" | "trainable"
CLASSIFIER_BACKEND=keyword

# Retrieval backend: "bm25" | "faiss"
RETRIEVAL_BACKEND=bm25
FAISS_MODEL_NAME=all-MiniLM-L6-v2

# Lane routing strategy: "threshold" | "rl"
ROUTING_STRATEGY=threshold

# Confidence threshold for auto-reply routing (0.0 – 1.0)
CONFIDENCE_THRESHOLD=0.65

# Classifier confidence calibration (Platt scaling). Defaults False pending
# held-out validation — see Roadmap, Phase 5B.
CALIBRATION_ENABLED=False

# Chair assignment strategy: "intent_mapping" (learned/RL planned)
CHAIR_ROUTING_STRATEGY=intent_mapping

# Model provider: "anthropic_api" | "local" | "template"
MODEL_PROVIDER=anthropic_api
ANTHROPIC_API_KEY=sk-ant-...
# Required when MODEL_PROVIDER=local (Ollama-compatible endpoint)
# OLLAMA_BASE_URL=http://localhost:11434

# Database backend: "sqlite" | "postgresql"
DATABASE_URL=sqlite:///./conference_email.db
```

> Known status: with calibration enabled, in-sample routing accuracy improves from 74.1% to 94.8% (Phase 5B) — but `CALIBRATION_ENABLED` stays `False` by default until validated on held-out data, and the Analytics reliability diagram flags in-sample results with an amber caveat rather than presenting them as ground truth.

---

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js 18+
- An Anthropic API key (for `MODEL_PROVIDER=anthropic_api`) — not required for `local` or `template`
- Docker Desktop (optional, for one-command spin-up)

### Quick Start (Docker)

```bash
docker compose up --build
```

### Backend Setup (manual)

```bash
cd backend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY if using MODEL_PROVIDER=anthropic_api

# Run migrations
alembic upgrade head

# Start the server
uvicorn main:app --reload
```

API available at `http://localhost:8000`
Interactive docs at `http://localhost:8000/docs`

### Frontend Setup (manual)

```bash
cd frontend

npm install
cp ../.env.example .env.local
# Set NEXT_PUBLIC_API_URL=http://localhost:8000

npm run dev
```

App available at `http://localhost:3000`

### Seed the Database

```bash
cd backend
python scripts/seed.py
```

This loads the toy email dataset — including the multi-chair dataset — and runs the full pipeline on each.

### Run the Eval Harness

```bash
cd backend
python scripts/run_eval.py
```

Runs the pipeline against the ground truth set and reports sklearn classification metrics as a JSON report.

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Service health check |
| POST | `/api/v1/emails` | Ingest a new email |
| GET | `/api/v1/emails` | List emails (filterable by status, lane) |
| GET | `/api/v1/emails/{id}` | Get single email with full pipeline output |
| POST | `/api/v1/pipeline/run/{id}` | Run full pipeline on an email |
| GET | `/api/v1/drafts/{id}` | Get current draft for an email |
| PATCH | `/api/v1/drafts/{id}/approve` | Approve, edit, or reroute a draft |
| GET | `/api/v1/analytics/summary` | Dashboard metrics |
| GET | `/api/v1/audit` | Paginated, filterable audit log |
| GET | `/api/v1/retrieval/info` | Active retriever backend + index stats |
| POST | `/api/v1/train/classifier` | Train the trainable classifier backend |

---

## Domain Model

### Email Intents
`FAQ_DEADLINE` · `FAQ_FORMAT` · `FAQ_SUBMISSION` · `REVIEW_ASSIGNMENT` · `VISA_LETTER` · `APPEAL` · `AMBIGUOUS` · `OTHER` (extended set as classifier taxonomy grows — see Roadmap, Phase 6A)

### Routing Lanes
`AUTO_REPLY` — answered automatically from KB
`HUMAN_REVIEW` — routed to a specific chair's queue with an AI draft (Phase 6A)

### Chairs (new, Phase 6A)
Program Chair · Diversity & Ethics Chair · Local Arrangements Chair · Publicity/Sponsorship Chair · General Chair (fallback). Each chair owns a configurable list of intent/topic areas; `Email.assigned_chair_id` records the assignment, and reroutes between chairs are captured in the audit log as a future training signal for a learned assignment strategy.

### Email Lifecycle
`PENDING` → `CLASSIFIED` → `ROUTED` → `DRAFT_GENERATED` → `APPROVED` → `SENT` → `ARCHIVED`

> Note: pipeline-assigned statuses are uppercase; action-endpoint statuses (approve/reroute) are lowercase in the current implementation.

---

## Roadmap

### Phase 0 — Scaffold ✅
Monorepo structure, FastAPI backend, Next.js 14 frontend skeleton, config flags, async SQLAlchemy + Alembic.

### Phase 1 — Data Layer + Pipeline ✅
Toy dataset, knowledge base, all 5 pipeline modules, REST endpoints, seed script.

### Phase 2 — Full UI ✅
Full Next.js 14 frontend — dashboard, split-pane review queue, auto-replies, recharts-based analytics, audit timeline; hooks for queue/analytics/audit/actions; dark-mode design system.

### Phase 3 — Research Extensions ✅
Real pagination/filtering audit endpoint, PostgreSQL migration readiness, local LLM backend, trainable sentence-transformers + LogisticRegression classifier, epsilon-greedy RL bandit router wired into approve/reroute feedback.

### Phase 4 — Retrieval, Eval & Reporting ✅
FAISS retriever, expanded ground-truth eval set, `scripts/run_eval.py` CLI, living progress PDF generator.

### Phase 5 — Calibration, Fusion & Production Readiness ✅
- **5A**: Eval/tracing infrastructure; retrieval confirmed not the FAQ routing bottleneck; eval set expanded to 58 emails
- **5B**: Platt-scaling calibration; routing accuracy 74.1% → 94.8% in-sample; `CALIBRATION_ENABLED` defaults `False` pending held-out validation
- **5C**: Reciprocal rank fusion retriever — honest negative result, does not beat FAISS alone
- **5D**: Template drafter — third zero-dependency drafter backend, completing the set (`anthropic_api`, `local`, `template`)
- **5E**: SSE-based live queue updates + calibration reliability diagram in Analytics
- **5F**: Chair-edit diff view (LCS word-level diffing) + keyboard shortcuts in the review queue
- **5G**: Active-learning flagging (`low_confidence`, `meaningful_edit` signals; candidates endpoint; no auto-retraining yet)
- **5H**: Model-agnostic Drafter adapter specification
- **5I**: Docker Compose (live-verified) + three-job secret-free CI on GitHub Actions
- **5J**: Demo walkthrough recording — pending

### Phase 6 — Multi-Chair Routing 🔄 (in progress)
- **6A**: Multi-chair routing backend — DB migration complete (`Chair` table, `Email.assigned_chair_id`, 5 chairs seeded); classifier intent taxonomy extended to give every chair a genuine auto-routing path; `chair_router.py` (intent-to-chair strategy) in progress; toy dataset covering all 5 chairs
- **6B** (planned): Frontend for chair assignment — assigned-chair badges, filter-by-chair, reroute-to-chair dropdown, routing-rationale panel, per-chair analytics
- **Held-out validation** (planned): validate calibration (5B) on held-out data before enabling by default
- **Real conference dataset** (planned): pending AAAI dataset approval

**Outstanding blockers:** NCSA Delta GPU allocation (for local draft generation) is still pending. The **policy corpus is now the real AAAI-27 knowledge base** (56 chunks); the **email dataset remains synthetic** (toy emails) pending real conference email traffic.

---

## Research Context

This system is developed as part of a research initiative at the **Melady Lab, University of Southern California**, exploring the application of AI pipelines to academic conference operations.

The architecture supports ongoing research in:
- Active learning from human reviewer decisions
- Online reinforcement learning for conference email routing using contextual bandits with human-in-the-loop feedback
- Learned, feedback-driven chair assignment — using reroute events as training signal (Phase 6+)
- Retrieval-augmented generation grounded in conference policies
- Evaluation of AI-assisted human-in-the-loop workflows

---

## Contributing

This is an active research project. If you are a collaborator:

1. Branch from `main`
2. Work in feature branches (`feature/phase-6a-chair-routing`, etc.)
3. All pipeline changes must preserve the `classify → retrieve → route → draft` interface contracts
4. Do not hardcode model names anywhere — code, comments, docs, UI, or commit messages. Use only capability-descriptive identifiers (`anthropic_api`, `local`, `template`)

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

*Built for the Melady Lab, USC · Conference Email Automation Research*