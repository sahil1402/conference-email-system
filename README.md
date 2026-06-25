# ConfMail — Automated Conference Email Reply & Routing System

> An AI-powered email management platform for academic conference organizations. Built for the Melady Lab at USC, targeting venues like AAAI, NeurIPS, ICML, and ICLR.

![Status](https://img.shields.io/badge/status-MVP%20in%20progress-orange)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Next.js](https://img.shields.io/badge/Next.js-14-black)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Overview

Conference program chairs receive hundreds of emails per cycle — submission deadline questions, formatting queries, visa letter requests, review conflicts, and appeals. Most are repetitive and answerable from public policy documents. A small fraction require genuine human judgment.

ConfMail separates these two classes automatically.

**FAQ Lane** — High-confidence, policy-grounded emails are answered automatically. No hallucinated policies. Every response is traced to a source document.

**Human Review Lane** — Novel, ambiguous, or sensitive emails are routed to a chair queue with an AI-generated draft. Chairs can approve, edit, or reroute with a full audit trail.

The system is designed as a research platform: every component (classifier, retriever, router, drafter) is modular and trainable, with a roadmap toward reinforcement-learning-based routing and local deployment for conferences with external API restrictions.

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
│  Retriever  │  ── BM25 search over FAQ knowledge base
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Router    │  ── Rule-based routing (Phase 1) → RL routing (Phase 2)
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

| Stage | Phase 1 | Phase 2 |
|---|---|---|
| Classifier | Prompt-based via AI API | Fine-tuned local model |
| Retriever | BM25 over JSON KB | Vector DB (FAISS/Chroma) |
| Router | Rule-based (confidence threshold) | Reinforcement learning |
| Drafter | AI API with policy grounding | Fine-tuned drafter |

### Key Design Principles

- **Separation of concerns** — Classifier, Retriever, Router, Drafter, Persistence, and UI are fully decoupled
- **Swappable backends** — All AI components are behind a config flag (`MODEL_PROVIDER`, `RETRIEVAL_BACKEND`, `ROUTING_STRATEGY`)
- **Trainable by design** — Every pipeline stage outputs structured data suitable for supervised fine-tuning
- **No hallucinated policies** — All auto-replies are grounded in retrieved knowledge base entries with source citations
- **Full auditability** — Every action (classification, routing, approval, edit) is logged with actor, timestamp, and metadata

---

## Tech Stack

### Backend
- **Python 3.11+** with **FastAPI** — async REST API
- **SQLAlchemy (async)** + **SQLite** — persistence layer (swappable to PostgreSQL)
- **Alembic** — database migrations
- **Pydantic v2** — schema validation and serialization
- **rank-bm25** — FAQ retrieval (Phase 1)
- **Anthropic API** — classification and draft generation

### Frontend
- **Next.js 14** (App Router) with **TypeScript**
- **Tailwind CSS v3** + **shadcn/ui** — component library
- **lucide-react** — icons

### Infrastructure
- Monorepo structure (`backend/` + `frontend/`)
- Environment-driven configuration via `.env`
- Alembic migrations for schema evolution

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
│   │   │   └── config.py              # 4 swappable backend flags
│   │   ├── api/
│   │   │   └── routes/
│   │   │       ├── emails.py          # Email ingestion + retrieval
│   │   │       ├── pipeline.py        # Classification + routing
│   │   │       ├── drafts.py          # Draft approval workflow
│   │   │       └── analytics.py       # Dashboard metrics
│   │   ├── pipeline/
│   │   │   ├── classifier/            # Intent classification
│   │   │   ├── retriever/             # BM25 FAQ retrieval
│   │   │   ├── router/                # Routing decision logic
│   │   │   └── drafter/               # AI draft generation
│   │   ├── models/
│   │   │   ├── enums.py               # EmailIntent, RoutingLane, etc.
│   │   │   └── schemas.py             # Pydantic v2 contracts
│   │   └── db/
│   │       ├── database.py            # Async SQLAlchemy setup
│   │       ├── models.py              # ORM models (3 tables)
│   │       └── repositories/          # Data access layer
│   ├── data/
│   │   ├── toy_emails.json            # 25 labeled toy emails
│   │   └── faq_kb.json                # 20 FAQ knowledge base entries
│   ├── migrations/                    # Alembic migrations
│   └── scripts/
│       └── seed.py                    # DB seeding script
└── frontend/
    └── src/
        ├── app/
        │   ├── layout.tsx             # Root layout with sidebar
        │   ├── dashboard/page.tsx
        │   ├── queue/page.tsx
        │   ├── auto-replies/page.tsx
        │   └── audit/page.tsx
        ├── components/
        │   ├── layout/                # Sidebar, Header, PageWrapper
        │   ├── email/                 # EmailCard, StatusBadge, ConfidenceBar
        │   ├── pipeline/              # Classification, Retrieval, Routing panels
        │   └── dashboard/             # StatsCard, Charts, ActivityFeed
        ├── lib/
        │   ├── api.ts                 # Typed API client
        │   └── utils.ts
        └── types/
            └── index.ts               # TypeScript types mirroring backend schemas
```

---

## Configuration

All backend behavior is controlled by 4 flags in `backend/.env`:

```env
# AI provider: "anthropic_api" | "local"
MODEL_PROVIDER=anthropic_api

# Confidence threshold for auto-reply routing (0.0 – 1.0)
CONFIDENCE_THRESHOLD=0.75

# Retrieval backend: "bm25" | "vector"
RETRIEVAL_BACKEND=bm25

# Routing strategy: "rule_based" | "rl"
ROUTING_STRATEGY=rule_based

# Anthropic API key (required when MODEL_PROVIDER=anthropic_api)
ANTHROPIC_API_KEY=sk-ant-...

# Database URL (defaults to SQLite)
DATABASE_URL=sqlite:///./conference_email.db
```

---

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js 18+
- An Anthropic API key (for Phase 2 onwards)

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

This loads all 25 toy emails and runs the full pipeline on each.

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
| POST | `/api/v1/drafts/{id}/approve` | Approve, edit, or reroute a draft |
| GET | `/api/v1/analytics/summary` | Dashboard metrics |

---

## Domain Model

### Email Intents
`FAQ_DEADLINE` · `FAQ_FORMAT` · `FAQ_SUBMISSION` · `REVIEW_ASSIGNMENT` · `VISA_LETTER` · `APPEAL` · `AMBIGUOUS` · `OTHER`

### Routing Lanes
`AUTO_REPLY` — answered automatically from KB
`HUMAN_REVIEW` — routed to chair queue with AI draft

### Email Lifecycle
`PENDING` → `CLASSIFIED` → `ROUTED` → `DRAFT_GENERATED` → `APPROVED` → `SENT` → `ARCHIVED`

---

## Roadmap

### Phase 0 — Scaffold ✅
Monorepo structure, FastAPI backend, Next.js frontend, database schema, Alembic migrations, domain enums and schemas.

### Phase 1 — Data Layer + Pipeline Stubs 🔄
Toy dataset (25 emails), FAQ knowledge base (20 entries), repository layer, pipeline stubs, seeded database, all API routes live.

### Phase 2 — Live Pipeline
Real BM25 retriever, prompt-based classifier via Anthropic API, rule-based router with confidence thresholds, AI draft generation with policy citations.

### Phase 3 — Full UI
Dashboard with analytics, email queue with split-pane view, classification/retrieval/routing panels, approval workflow, audit log.

### Phase 4 — Research Extensions
Trainable classifier (fine-tuning pipeline), RL-based router, vector retrieval (FAISS), local deployment mode, eval harness with ground truth labels.

---

## Research Context

This system is developed as part of a research initiative at the **Melady Lab, University of Southern California**, exploring the application of AI pipelines to academic conference operations.

The architecture is designed to support future research in:
- Active learning from human reviewer decisions
- Reinforcement learning for routing policy optimization
- Retrieval-augmented generation grounded in conference policies
- Evaluation of AI-assisted human-in-the-loop workflows

---

## Contributing

This is an active research project. If you are a collaborator:

1. Branch from `main`
2. Work in feature branches (`feature/phase-2-classifier`, etc.)
3. All pipeline changes must preserve the `classify → retrieve → route → draft` interface contracts
4. Do not hardcode model names in design documents or comments

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

*Built for the Melady Lab, USC · Conference Email Automation Research*
