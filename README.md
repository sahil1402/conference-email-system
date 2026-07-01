# ConfMail — Automated Conference Email Reply & Routing System

> An AI-powered email management platform for academic conference organizations. Built for the Melady Lab at USC, targeting venues like AAAI, NeurIPS, ICML, and ICLR.

![Status](https://img.shields.io/badge/status-Phase%200--4%20complete-brightgreen)
![Tests](https://img.shields.io/badge/tests-47%2F47%20passing-brightgreen)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Next.js](https://img.shields.io/badge/Next.js-14-black)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Overview

Conference program chairs receive hundreds of emails per cycle — submission deadline questions, formatting queries, visa letter requests, review conflicts, and appeals. Most are repetitive and answerable from public policy documents. A small fraction require genuine human judgment.

ConfMail separates these two classes automatically.

**FAQ Lane** — High-confidence, policy-grounded emails are answered automatically. No hallucinated policies. Every response is traced to a source document.

**Human Review Lane** — Novel, ambiguous, or sensitive emails are routed to a chair queue with an AI-generated draft. Chairs can approve, edit, or reroute with a full audit trail.

The system is designed as a research platform: every component (classifier, retriever, router, drafter, database) is modular and config-flag-swappable, with reinforcement-learning-based routing and local-only deployment already implemented for conferences with external API restrictions.

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
│   Router    │  ── Confidence-threshold or bandit-based routing decision
└──────┬──────┘
       │
   ┌───┴───┐
   │       │
   ▼       ▼
 FAQ    Human
 Lane   Review
   │       │
   │       ▼
   │  ┌──────────┐
   │  │  Drafter │  ── AI draft generation with policy citations
   │  └──────────┘
   │       │
   ▼       ▼
Auto-   Approval
Reply    Queue
```

### Pipeline Stages

Every stage is a swappable, config-flag-controlled module — none of these are phase-gated placeholders; both backends are live for each stage.

| Stage | Backend Options | Notes |
|---|---|---|
| Classifier | `keyword` · `trainable` | Trainable backend uses sentence-transformers embeddings + LogisticRegression, exposed via `/api/v1/train/classifier` |
| Retriever | `bm25` · `faiss` | FAISS backend uses sentence-transformers (`all-MiniLM-L6-v2`) with `IndexFlatIP` cosine similarity |
| Router | `threshold` · `rl` | RL backend is an online, epsilon-greedy contextual bandit (ε=0.15) updated on every approve/reroute |
| Drafter | `anthropic` · `local` | Local backend targets an Ollama-compatible endpoint; pending GPU compute for production use |
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
- **scikit-learn** — trainable classifier (LogisticRegression) and eval metrics
- **Anthropic API** — classification and draft generation (with local/Ollama-compatible fallback)
- **pytest** + **pytest-asyncio** — 47/47 tests passing

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
│   │   │   ├── router/                # threshold + RL bandit router
│   │   │   └── drafter/               # anthropic + local draft generation
│   │   ├── models/
│   │   │   ├── enums.py               # EmailIntent, RoutingLane, EmailStatus
│   │   │   └── schemas.py             # Pydantic v2 contracts
│   │   └── db/
│   │       ├── database.py            # Async SQLAlchemy setup
│   │       ├── models.py              # ORM models (3 tables)
│   │       └── repositories/          # Data access layer
│   ├── data/
│   │   ├── toy_emails.json            # 30 labeled toy emails, 8 intents
│   │   └── faq_kb.json                # 45 policy/FAQ knowledge base chunks
│   ├── data/eval/
│   │   └── ground_truth.json          # 40-email eval set covering all 8 intents
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
        │   ├── analytics/page.tsx     # recharts-based analytics
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

---

## Configuration

All backend behavior is controlled by config flags in `backend/.env`:

```env
# Classifier backend: "keyword" | "trainable"
CLASSIFIER_BACKEND=keyword

# Retrieval backend: "bm25" | "faiss"
RETRIEVAL_BACKEND=bm25
FAISS_MODEL_NAME=all-MiniLM-L6-v2

# Router strategy: "threshold" | "rl"
ROUTER_BACKEND=threshold

# Confidence threshold for auto-reply routing (0.0 – 1.0)
CONFIDENCE_THRESHOLD=0.65

# Model provider: "anthropic" | "local"
MODEL_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
# Required when MODEL_PROVIDER=local (Ollama-compatible endpoint)
# OLLAMA_BASE_URL=http://localhost:11434

# Database backend: "sqlite" | "postgresql"
DATABASE_URL=sqlite:///./conference_email.db
```

> Note the current known limitation: with `CLASSIFIER_BACKEND=keyword` and `ROUTER_BACKEND=threshold`, baseline FAQ routing accuracy on the eval set is 6/15, as classifier confidence tends to sit below the 0.65 threshold. This is an active tuning target — see Roadmap, Phase 5.

---

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js 18+
- An Anthropic API key (for `MODEL_PROVIDER=anthropic`)

### Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# Run migrations
alembic upgrade head

# Start the server
uvicorn main:app --reload
```

API available at `http://localhost:8000`
Interactive docs at `http://localhost:8000/docs`

### Frontend Setup

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

This loads all 30 toy emails and runs the full pipeline on each.

### Run the Eval Harness

```bash
cd backend
python scripts/run_eval.py
```

Runs the pipeline against the 40-email ground truth set and reports sklearn classification metrics as a JSON report.

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
`FAQ_DEADLINE` · `FAQ_FORMAT` · `FAQ_SUBMISSION` · `REVIEW_ASSIGNMENT` · `VISA_LETTER` · `APPEAL` · `AMBIGUOUS` · `OTHER`

### Routing Lanes
`AUTO_REPLY` — answered automatically from KB
`HUMAN_REVIEW` — routed to chair queue with AI draft

### Email Lifecycle
`PENDING` → `CLASSIFIED` → `ROUTED` → `DRAFT_GENERATED` → `APPROVED` → `SENT` → `ARCHIVED`

> Note: pipeline-assigned statuses are uppercase; action-endpoint statuses (approve/reroute) are lowercase in the current implementation.

---

## Roadmap

### Phase 0 — Scaffold ✅
Monorepo structure, FastAPI backend, Next.js 14 frontend skeleton, 4 config flags, async SQLAlchemy + Alembic (3 tables).

### Phase 1 — Data Layer + Pipeline ✅ (16/16 tests)
Toy dataset (30 emails, 8 intents), knowledge base (45 policy chunks), all 5 pipeline modules, REST endpoints, seed script.

### Phase 2 — Full UI ✅
Full Next.js 14 frontend — dashboard, split-pane review queue, auto-replies, recharts-based analytics, audit timeline; hooks for queue/analytics/audit/actions; dark-mode design system.

### Phase 3 — Research Extensions ✅ (36 tests)
Real pagination/filtering audit endpoint, PostgreSQL migration readiness (asyncpg + Alembic checkpoint), local LLM backend (Ollama-compatible via `MODEL_PROVIDER`), trainable sentence-transformers + LogisticRegression classifier, epsilon-greedy RL bandit router wired into approve/reroute feedback.

### Phase 4 — Retrieval, Eval & Reporting ✅ (42 tests)
- **4A**: FAISS retriever (faiss-cpu + sentence-transformers, cosine similarity, lazy index build, `/api/v1/retrieval/info`)
- **4B**: 40-email ground truth eval set, `scripts/run_eval.py` CLI; baseline finding — FAQ routing accuracy 6/15, classifier confidence sitting below the 0.65 FAQ threshold
- **4C**: `scripts/generate_progress_pdf.py` — living progress PDF documenting all phases

**Outstanding blockers for full production use:** NCSA Delta GPU allocation (for local LLM draft generation) and a real conference email dataset (current dataset is synthetic).

### Phase 5 — Calibration, Fusion & Production Readiness 🔄 (planned, GPU-independent)
- **5A**: Eval & observability foundation — per-email tracing, retrieval-only metrics (recall@k, nDCG), expanded boundary-case eval set
- **5B**: Classifier calibration — Platt/temperature scaling to address the 6/15 baseline
- **5C**: Retrieval fusion — reciprocal rank fusion between BM25 and FAISS
- **5D**: No-API drafter backend — template-based draft generation with zero AI dependency
- **5E**: Live queue (SSE/WebSocket) + calibration reliability diagram in Analytics
- **5F**: Chair-edit diff view + keyboard shortcuts in the review queue
- **5G**: Active-learning flagging of low-confidence-but-approved cases
- **5H**: Model-agnostic Drafter adapter-interface specification
- **5I**: Docker Compose + CI (47 tests + `run_eval.py` on every push)
- **5J**: Demo walkthrough recording

---

## Research Context

This system is developed as part of a research initiative at the **Melady Lab, University of Southern California**, exploring the application of AI pipelines to academic conference operations.

The architecture supports ongoing research in:
- Active learning from human reviewer decisions
- Online reinforcement learning for conference email routing using contextual bandits with human-in-the-loop feedback
- Retrieval-augmented generation grounded in conference policies
- Evaluation of AI-assisted human-in-the-loop workflows

---

## Contributing

This is an active research project. If you are a collaborator:

1. Branch from `main`
2. Work in feature branches (`feature/phase-5a-eval-observability`, etc.)
3. All pipeline changes must preserve the `classify → retrieve → route → draft` interface contracts
4. Do not hardcode model names in design documents, code comments, or communications

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

*Built for the Melady Lab, USC · Conference Email Automation Research*