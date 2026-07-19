# Re-evaluate Open Tickets on Policy Change — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a chair-triggered "Re-evaluate open tickets" sweep that re-drafts exactly the open tickets whose retrieval shifted after KB edits, leaving unaffected tickets untouched.

**Architecture:** At ingest we persist each email's *retrieval context* (the exact query string + intent that were sent to the retriever, and the top-k policy ids that grounded the draft). A new `POST /api/v1/policies/reevaluate` schedules one background sweep (`reevaluate_open_tickets`). The sweep re-runs the retriever per open ticket using the stored query (no model call), and re-drafts iff the fresh top-k *set* differs from the stored set. Chair-edited drafts are skipped. A transient `redrafting` boolean on the email drives a live "re-drafting…" badge via the existing SSE audit stream.

**Tech Stack:** Python 3.12 · FastAPI · async SQLAlchemy 2.0 · Alembic · pytest + pytest-asyncio · Next.js 14 + TypeScript · React Query.

## Global Constraints

- **No model names in source or docs.** Read the model id from `settings.DRAFT_MODEL`; never hardcode. (CLAUDE.md engineering rule.)
- **No Claude attribution in commits.** Do NOT add `Co-Authored-By: Claude` or `Claude-Session:` trailers. (User memory: `no-claude-commit-attribution`.)
- **DB access only through repositories.** The pipeline/API layers never touch SQLAlchemy directly. (Architecture rule 5.)
- **Modules stay separate + typed.** Re-eval is new pipeline code (`app/pipeline/reevaluation.py`); it reuses the drafter/router/retriever, never reimplements them.
- **Backend TDD via pytest.** Every backend task writes a failing test first, then the minimal code. Run `cd backend && python -m pytest -m "not ml"` for the fast gate.
- **Frontend gated on `tsc`.** Every frontend task ends with `cd frontend && npx tsc --noEmit` passing (0 errors). No hex colors — use CSS-var tokens (`var(--…)`). Avoid TS2802 spread-over-Set (`Array.from`, not `[...set]`).
- **Hermetic tests.** The autouse conftest fixture forces `MODEL_PROVIDER=fallback` / no key / `QUERY_STRATEGY=prefix` / `RETRIEVAL_BACKEND=bm25` / `WARM_RETRIEVER_ON_STARTUP=False`. Tests assert re-drafting happens without a real model call.
- **Actor stand-in.** Governance actor is the hardcoded `"Chair1"` stand-in until auth (frontend `ACTOR` constant in `hooks/usePolicies.ts`).

Spec: `docs/REEVALUATE_ON_POLICY_CHANGE_DESIGN.md`.

---

## File Structure

| File | Responsibility | New/Modify |
|---|---|---|
| `backend/migrations/versions/<rev>_phase_g_email_redraft.py` | Add `emails.redrafting` + `emails.retrieval_context` | New |
| `backend/app/db/models.py` | `Email.redrafting` + `Email.retrieval_context` columns | Modify |
| `backend/app/pipeline/orchestrator.py` | Persist `retrieval_context` (query + intent + retrieved_ids) at ingest | Modify |
| `backend/app/repositories/email_repository.py` | `get_open_tickets`, `set_redrafting`, `save_redraft` | Modify |
| `backend/app/pipeline/reevaluation.py` | `reevaluate_open_tickets()` — gate + re-draft + audit + SSE | New |
| `backend/app/api/v1/policies.py` | `POST /policies/reevaluate` → schedule sweep, return open count | Modify |
| `backend/app/api/v1/emails.py` | expose `redrafting` + `retrieval_context` in `_email_to_dict` | Modify |
| `frontend/src/types/index.ts` | `Email.redrafting` field | Modify |
| `frontend/src/lib/api/policies.ts` | `reevaluatePolicies()` client | Modify |
| `frontend/src/hooks/usePolicies.ts` | `useReevaluatePolicies()` hook | Modify |
| `frontend/src/app/knowledge-base/page.tsx` | "Re-evaluate open tickets" button | Modify |
| `frontend/src/components/email/EmailListItem.tsx` | "re-drafting…" badge | Modify |
| `frontend/src/components/email/EmailDetail.tsx` | "re-drafting…" banner | Modify |

**`retrieval_context` shape** (the faithful, minimal form — store the exact retriever inputs so the gate is a pure lookup with zero re-derivation):

```json
{ "query": "<the exact string passed to retriever.retrieve>",
  "intent": "<the retrieval_intent passed alongside it>",
  "retrieved_ids": ["policy_137", "int_deadline-extended", "policy_104"] }
```

---

## Task 1: Schema — `redrafting` + `retrieval_context` on `emails`

**Files:**
- Modify: `backend/app/db/models.py:40-43` (add two columns after the `draft` JSON column)
- Create: `backend/migrations/versions/c1d2e3f4a5b6_phase_g_email_redraft.py`
- Test: `backend/tests/test_email_redraft_columns.py`

**Interfaces:**
- Produces: `Email.redrafting: bool` (default `False`), `Email.retrieval_context: dict | None` (JSON, nullable). Later tasks read/write both.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_email_redraft_columns.py`. **Test convention (whole repo):** `asyncio_mode = "auto"` (in `pyproject.toml`), so async tests need NO `@pytest.mark.asyncio`; there is NO shared `db_session` fixture — each test module defines its own in-memory `session` fixture (mirroring `tests/test_policy_kb_layers.py`).

```python
"""The emails table carries the re-eval columns (Phase G)."""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Email


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def test_email_model_has_redraft_columns():
    cols = Email.__table__.columns
    assert "redrafting" in cols
    assert "retrieval_context" in cols
    # redrafting is a non-null boolean defaulting to False.
    assert cols["redrafting"].nullable is False
    # retrieval_context is nullable JSON.
    assert cols["retrieval_context"].nullable is True


async def test_redrafting_defaults_false_on_insert(session):
    email = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
    session.add(email)
    await session.commit()
    await session.refresh(email)
    assert email.redrafting is False
    assert email.retrieval_context is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_email_redraft_columns.py -v`
Expected: FAIL — `AttributeError`/`KeyError` on `redrafting` (column not defined).

- [ ] **Step 3: Add the columns to the ORM model**

In `backend/app/db/models.py`, immediately after the `draft` column (line 43, `draft: Mapped[dict | None] = mapped_column(JSON, nullable=True)`), add:

```python
    # Re-evaluation (Phase G). ``retrieval_context`` captures the exact retriever
    # inputs at ingest — {"query": str, "intent": str, "retrieved_ids": [...]} —
    # so a KB-change sweep can re-run retrieval with no model call and compare the
    # grounding set. ``redrafting`` is the transient in-progress flag surfaced as
    # the "re-drafting…" badge; set when queued, cleared when the new draft lands.
    retrieval_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    redrafting: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0", index=True
    )
```

`Boolean` and `JSON` are already imported at `models.py:10`.

- [ ] **Step 4: Write the Alembic migration**

Create `backend/migrations/versions/c1d2e3f4a5b6_phase_g_email_redraft.py`:

```python
"""phase G: emails.redrafting + emails.retrieval_context

Revision ID: c1d2e3f4a5b6
Revises: a7b8c9d0e1f2
Create Date: 2026-07-19

Adds the two columns the re-evaluate-on-policy-change sweep needs:
- retrieval_context (JSON, nullable): the exact retriever inputs captured at
  ingest so the sweep re-runs retrieval with no model call.
- redrafting (Boolean, default False): transient in-progress flag for the UI.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("emails", sa.Column("retrieval_context", sa.JSON(), nullable=True))
    op.add_column(
        "emails",
        sa.Column(
            "redrafting",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_index("ix_emails_redrafting", "emails", ["redrafting"])


def downgrade() -> None:
    op.drop_index("ix_emails_redrafting", table_name="emails")
    op.drop_column("emails", "redrafting")
    op.drop_column("emails", "retrieval_context")
```

> The current migration head is `a7b8c9d0e1f2` (`phase_f_policy_audit`). Confirm before writing: `cd backend && python -m alembic heads` should print `a7b8c9d0e1f2`. If it differs, set `down_revision` to whatever `alembic heads` reports.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_email_redraft_columns.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Verify the migration applies on a scratch DB**

Run: `cd backend && DATABASE_URL="sqlite:///./_scratch_reeval.db" python -m alembic upgrade head && rm -f _scratch_reeval.db`
Expected: `Running upgrade a7b8c9d0e1f2 -> c1d2e3f4a5b6` with no error.

- [ ] **Step 7: Commit**

```bash
cd /projects/bdem/jpang1/email_agent/conference-email-system
git add backend/app/db/models.py backend/migrations/versions/c1d2e3f4a5b6_phase_g_email_redraft.py backend/tests/test_email_redraft_columns.py
git commit -m "feat(db): add emails.redrafting + retrieval_context for re-eval sweep"
```

---

## Task 2: Persist `retrieval_context` at ingest

**Files:**
- Modify: `backend/app/pipeline/orchestrator.py` (capture the retriever inputs; add to the persisted `record`)
- Test: `backend/tests/test_orchestrator_retrieval_context.py`

**Interfaces:**
- Consumes: `Email.retrieval_context` column (Task 1).
- Produces: after `process_email`, the persisted email row carries `retrieval_context = {"query": <str>, "intent": <str>, "retrieved_ids": [<policy_id>, ...]}`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_orchestrator_retrieval_context.py`. (Same harness note as Task 1: local `session` fixture, no `@pytest.mark.asyncio`. This mirrors `tests/test_chair_routing_integration.py`, which drives `EmailPipeline` against an in-memory DB with no seeded policies — the BM25 retriever returns `[]` gracefully, so the test does not depend on retrieval hits.)

```python
"""process_email persists the retrieval context used to ground the draft."""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.pipeline.orchestrator import EmailPipeline
from app.repositories.email_repository import EmailRepository


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_process_email_persists_retrieval_context(session):
    pipeline = EmailPipeline()
    result = await pipeline.process_email(
        {
            "from": "author@uni.edu",
            "to": "chair@conf.org",
            "subject": "Question about the submission deadline",
            "body": "Can I get an extension on the paper submission deadline?",
            "timestamp": "",
        },
        session,
    )

    email = await EmailRepository().get_email_by_id(session, result.email_id)
    ctx = email.retrieval_context
    assert ctx is not None
    assert isinstance(ctx["query"], str) and ctx["query"]
    assert "intent" in ctx
    # The stored ids are exactly the top-k that grounded this draft (both derive
    # from the same retrieval call, so they match even when the set is empty).
    assert ctx["retrieved_ids"] == [c.policy_id for c in result.retrieved_chunks]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_orchestrator_retrieval_context.py -v`
Expected: FAIL — `ctx is None` (orchestrator does not persist it yet).

- [ ] **Step 3: Capture the retriever inputs in `process_email`**

In `backend/app/pipeline/orchestrator.py`, the retriever call already computes `query` and `retrieval_intent` (lines ~175-195). Those two locals are in scope through persistence. In the `record` dict (currently `orchestrator.py:246-258`), add the `retrieval_context` key alongside the existing pipeline outputs:

```python
        record = {
            "sender": email_data.get("from") or email_data.get("sender") or "unknown@unknown",
            "sender_name": email_data.get("sender_name"),
            "subject": subject,
            "body": body,
            "status": _LIFECYCLE_STATUS[status],
            "classification": classification.model_dump(),
            "routing": routing.model_dump(),
            "draft": draft.model_dump(),
            "assigned_chair_id": (
                chair_assignment.chair_id if chair_assignment else None
            ),
            # Exact retriever inputs + the grounding set, so a later KB-change
            # sweep can re-run retrieval with no model call and compare.
            "retrieval_context": {
                "query": query,
                "intent": retrieval_intent,
                "retrieved_ids": [c.policy_id for c in retrieved_chunks],
            },
        }
```

(`query`, `retrieval_intent`, and `retrieved_chunks` are all already local variables at this point in the function.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_orchestrator_retrieval_context.py -v`
Expected: PASS.

- [ ] **Step 5: Run the orchestrator suite for regressions**

Run: `cd backend && python -m pytest tests/ -m "not ml" -k "orchestrator or pipeline or ingest" -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/pipeline/orchestrator.py backend/tests/test_orchestrator_retrieval_context.py
git commit -m "feat(pipeline): persist retrieval context (query+intent+ids) at ingest"
```

---

## Task 3: Email repository — open tickets, redrafting flag, save re-draft

**Files:**
- Modify: `backend/app/repositories/email_repository.py` (three new methods)
- Test: `backend/tests/test_email_repo_reeval.py`

**Interfaces:**
- Consumes: `Email.redrafting`, `Email.retrieval_context`, `Email.draft`, `Email.routing` columns.
- Produces:
  - `EmailRepository.get_open_tickets(db) -> list[Email]` — all `status == "draft_generated"`, ordered by id.
  - `EmailRepository.set_redrafting(db, email_id: str, value: bool) -> Email | None`.
  - `EmailRepository.save_redraft(db, email_id: str, *, draft: dict, routing: dict, retrieval_context: dict) -> Email | None` — overwrite draft + routing + retrieval_context in one commit; also clears `redrafting`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_email_repo_reeval.py` (local `session` fixture, no `@pytest.mark.asyncio`):

```python
"""EmailRepository helpers for the re-evaluation sweep."""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Email
from app.models.enums import EmailStatus
from app.repositories.email_repository import EmailRepository


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _make_email(db, status: str) -> Email:
    e = Email(sender="a@b.com", subject="s", body="b", status=status)
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return e


async def test_get_open_tickets_returns_only_draft_generated(session):
    repo = EmailRepository()
    open_e = await _make_email(session, EmailStatus.DRAFT_GENERATED.value)
    await _make_email(session, EmailStatus.APPROVED.value)
    await _make_email(session, EmailStatus.PENDING.value)

    tickets = await repo.get_open_tickets(session)
    assert [t.id for t in tickets] == [open_e.id]


async def test_set_redrafting_toggles_flag(session):
    repo = EmailRepository()
    e = await _make_email(session, EmailStatus.DRAFT_GENERATED.value)

    updated = await repo.set_redrafting(session, str(e.id), True)
    assert updated is not None and updated.redrafting is True

    cleared = await repo.set_redrafting(session, str(e.id), False)
    assert cleared.redrafting is False


async def test_save_redraft_overwrites_and_clears_flag(session):
    repo = EmailRepository()
    e = await _make_email(session, EmailStatus.DRAFT_GENERATED.value)
    await repo.set_redrafting(session, str(e.id), True)

    saved = await repo.save_redraft(
        session,
        str(e.id),
        draft={"draft_text": "new", "placeholders": []},
        routing={"lane": "faq"},
        retrieval_context={"query": "q", "intent": "", "retrieved_ids": ["policy_1"]},
    )
    assert saved is not None
    assert saved.draft["draft_text"] == "new"
    assert saved.routing["lane"] == "faq"
    assert saved.retrieval_context["retrieved_ids"] == ["policy_1"]
    assert saved.redrafting is False  # cleared as part of the save


async def test_reeval_helpers_return_none_for_missing(session):
    repo = EmailRepository()
    assert await repo.set_redrafting(session, "999999", True) is None
    assert await repo.save_redraft(
        session, "999999", draft={}, routing={}, retrieval_context={}
    ) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_email_repo_reeval.py -v`
Expected: FAIL — `AttributeError: 'EmailRepository' object has no attribute 'get_open_tickets'`.

- [ ] **Step 3: Add the three methods**

In `backend/app/repositories/email_repository.py`, add these methods to `EmailRepository` (after `assign_chair`, before the `# --- reads ---` divider is fine — or in the reads/writes sections as noted). `select` is already imported at the top; import `EmailStatus` at the top of the file if not present.

Add the import near the existing `from app.db.models import Email`:

```python
from app.models.enums import EmailStatus
```

Add the methods:

```python
    async def get_open_tickets(self, db: AsyncSession) -> list[Email]:
        """Return every open ticket (status draft_generated), oldest id first.

        "Open" = has an auto-draft awaiting chair action and not yet approved or
        sent. These are the only tickets a KB-change sweep re-evaluates.
        """
        result = await db.execute(
            select(Email)
            .where(Email.status == EmailStatus.DRAFT_GENERATED.value)
            .order_by(Email.id)
        )
        return list(result.scalars().all())

    async def set_redrafting(
        self, db: AsyncSession, email_id: str, value: bool
    ) -> Email | None:
        """Set/clear the transient ``redrafting`` flag. Returns None if absent."""
        pk = _coerce_id(email_id)
        if pk is None:
            return None
        result = await db.execute(select(Email).where(Email.id == pk))
        email = result.scalar_one_or_none()
        if email is None:
            return None
        email.redrafting = value
        await db.commit()
        await db.refresh(email)
        return email

    async def save_redraft(
        self,
        db: AsyncSession,
        email_id: str,
        *,
        draft: dict,
        routing: dict,
        retrieval_context: dict,
    ) -> Email | None:
        """Persist a re-drafted ticket: overwrite draft + routing + retrieval
        context in one commit and clear ``redrafting``. Returns None if absent.

        Status is left as-is (draft_generated) — a re-draft replaces the pending
        draft in place; it does not change the lifecycle stage.
        """
        pk = _coerce_id(email_id)
        if pk is None:
            return None
        result = await db.execute(select(Email).where(Email.id == pk))
        email = result.scalar_one_or_none()
        if email is None:
            return None
        email.draft = draft
        email.routing = routing
        email.retrieval_context = retrieval_context
        email.redrafting = False
        await db.commit()
        await db.refresh(email)
        return email
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_email_repo_reeval.py -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/email_repository.py backend/tests/test_email_repo_reeval.py
git commit -m "feat(repo): open-ticket query + redrafting flag + save_redraft"
```

---

## Task 4: The re-evaluation sweep (`reevaluation.py`)

**Files:**
- Create: `backend/app/pipeline/reevaluation.py`
- Test: `backend/tests/test_reevaluation.py`

**Interfaces:**
- Consumes: `EmailRepository.get_open_tickets` / `set_redrafting` / `save_redraft` (Task 3); `AuditRepository.log_action`; `get_retriever()`; `EmailRouter`; `ResponseDrafter`; `ClassificationResult`; `Email.retrieval_context`.
- Produces:
  - `async def reevaluate_open_tickets(session_factory=async_session_factory) -> dict` — runs the sweep in its own session(s), returns `{"open": n, "redrafted": r, "skipped_edited": s, "unaffected": u}`.
  - `async def _fresh_topk_ids(retriever, ctx, top_k) -> list[str]` — helper re-running retrieval from stored ctx (used by tests too).

**Gate:** compare the fresh top-k as a **set** against the stored set — a mere score reshuffle (same chunks, different order) does not change what the draft is grounded on, so it must not trigger a re-draft.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_reevaluation.py` (local `session` fixture, no `@pytest.mark.asyncio`; the retriever is stubbed per-test via monkeypatch so the gate is fully controlled):

```python
"""The re-evaluate-open-tickets sweep: gate + re-draft + audit + flag."""

from contextlib import asynccontextmanager

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Email
from app.models.enums import EmailStatus
from app.pipeline.reevaluation import reevaluate_open_tickets
from app.repositories.audit_repository import AuditRepository
from app.repositories.email_repository import EmailRepository


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _factory(session):
    """A session_factory that yields the test's OWN session (so the sweep's
    writes are visible to the assertions) and never closes it."""

    @asynccontextmanager
    async def factory():
        yield session

    return factory


def _open_email(**ctx_over) -> Email:
    """An open ticket with a stored draft + retrieval context."""
    ctx = {"query": "deadline extension", "intent": "", "retrieved_ids": ["policy_101"]}
    ctx.update(ctx_over)
    return Email(
        sender="a@b.com",
        subject="Deadline?",
        body="Can I get a deadline extension?",
        status=EmailStatus.DRAFT_GENERATED.value,
        classification={"intent": "deadlines", "confidence": 0.6,
                        "reasoning": "x", "method": "keyword"},
        routing={"lane": "human_review", "reason": "x"},
        draft={"draft_text": "old", "notes_for_chair": None, "placeholders": [],
               "citations": ["policy_101"], "model_used": "fallback",
               "generation_metadata": {}},
        retrieval_context=ctx,
    )


class _StubRetriever:
    """Returns a fixed id list regardless of query, to drive the gate."""

    def __init__(self, ids):
        from app.pipeline.retriever import RetrievedChunk
        self._chunks = [
            RetrievedChunk(policy_id=i, title=i, content=f"body {i}", score=1.0)
            for i in ids
        ]

    async def retrieve(self, query, intent, top_k=3):
        return self._chunks[:top_k]


async def test_unaffected_ticket_is_not_redrafted(session, monkeypatch):
    session.add(_open_email())
    await session.commit()

    # Fresh retrieval returns the SAME id set as stored → not affected.
    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_101"])
    )
    stats = await reevaluate_open_tickets(session_factory=_factory(session))
    assert stats == {"open": 1, "redrafted": 0, "skipped_edited": 0, "unaffected": 1}


async def test_affected_ticket_is_redrafted_and_context_updated(session, monkeypatch):
    session.add(_open_email())
    await session.commit()

    # Fresh retrieval surfaces a different policy → affected → re-draft.
    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_999"])
    )
    stats = await reevaluate_open_tickets(session_factory=_factory(session))
    assert stats["redrafted"] == 1

    email = (await EmailRepository().get_open_tickets(session))[0]
    assert email.retrieval_context["retrieved_ids"] == ["policy_999"]
    assert email.redrafting is False
    trail = await AuditRepository().get_audit_trail(session, str(email.id))
    assert any(a.action == "ticket_redrafted" for a in trail)


async def test_chair_edited_ticket_is_skipped(session, monkeypatch):
    e = _open_email()
    e.draft = {**e.draft, "is_edited": True}
    session.add(e)
    await session.commit()

    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_999"])
    )
    stats = await reevaluate_open_tickets(session_factory=_factory(session))
    assert stats["skipped_edited"] == 1
    assert stats["redrafted"] == 0

    email = (await EmailRepository().get_open_tickets(session))[0]
    assert email.draft["draft_text"] == "old"  # untouched
    trail = await AuditRepository().get_audit_trail(session, str(email.id))
    assert any(a.action == "ticket_redraft_skipped_edited" for a in trail)


async def test_repeat_sweep_is_noop_after_redraft(session, monkeypatch):
    session.add(_open_email())
    await session.commit()
    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_999"])
    )
    first = await reevaluate_open_tickets(session_factory=_factory(session))
    assert first["redrafted"] == 1
    # Second sweep: stored ids now equal fresh ids → nothing to do.
    second = await reevaluate_open_tickets(session_factory=_factory(session))
    assert second["redrafted"] == 0
    assert second["unaffected"] == 1
```

> The sweep calls `session_factory()` as `async with session_factory() as db:`. `_factory` (defined above) yields the test's own session so assertions see the sweep's writes and never closes it. In production the default is `async_session_factory` (a real per-call session that closes on exit).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_reevaluation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.pipeline.reevaluation'`.

- [ ] **Step 3: Write `reevaluation.py`**

Create `backend/app/pipeline/reevaluation.py`:

```python
"""Re-evaluate open tickets after a KB change (the chair's "re-evaluate" button).

One sweep re-runs retrieval for every open ticket using the query captured at
ingest (no model call) and re-drafts only the tickets whose grounding set moved.
Chair-edited drafts are never clobbered. The sweep runs in its own DB session so
it can be scheduled as a FastAPI background task after the request returns.

Gate (design §3): a ticket is *affected* iff the set of fresh top-k policy ids
differs from the set stored at draft time. Comparing sets (not ordered lists)
means a pure score reshuffle — same chunks, different order — does not force a
needless re-draft.
"""

import logging

from app.core.config import settings
from app.db.database import async_session_factory
from app.pipeline.classifier import ClassificationResult
from app.pipeline.drafter import ResponseDrafter
from app.pipeline.retriever import get_retriever
from app.pipeline.router import LANE_HUMAN_REVIEW, EmailRouter
from app.repositories.audit_repository import AuditRepository
from app.repositories.email_repository import EmailRepository

logger = logging.getLogger(__name__)

_ACTOR = "reevaluation"


async def _fresh_topk_ids(retriever, ctx: dict, top_k: int):
    """Re-run retrieval from a ticket's stored context; return (ids, chunks)."""
    query = (ctx or {}).get("query") or ""
    intent = (ctx or {}).get("intent") or ""
    chunks = await retriever.retrieve(query, intent, top_k=top_k)
    return [c.policy_id for c in chunks], chunks


async def reevaluate_open_tickets(session_factory=async_session_factory) -> dict:
    """Sweep open tickets; re-draft the ones whose retrieval changed.

    Returns a summary: {"open", "redrafted", "skipped_edited", "unaffected"}.
    Best-effort per ticket — a failure on one ticket is logged, its ``redrafting``
    flag cleared, and the sweep continues.
    """
    email_repo = EmailRepository()
    audit_repo = AuditRepository()
    retriever = get_retriever()
    router = EmailRouter(strategy=settings.ROUTING_STRATEGY)
    drafter = ResponseDrafter(provider=settings.MODEL_PROVIDER)
    top_k = settings.MAX_RETRIEVED_CHUNKS

    stats = {"open": 0, "redrafted": 0, "skipped_edited": 0, "unaffected": 0}

    async with session_factory() as db:
        tickets = await email_repo.get_open_tickets(db)
        stats["open"] = len(tickets)

        for email in tickets:
            # A ticket already mid-redraft (a prior in-flight sweep) is left alone.
            if email.redrafting:
                continue
            ctx = email.retrieval_context or {}
            stored_ids = set(ctx.get("retrieved_ids") or [])

            fresh_ids_list, fresh_chunks = await _fresh_topk_ids(retriever, ctx, top_k)
            if set(fresh_ids_list) == stored_ids:
                stats["unaffected"] += 1
                continue

            email_id = str(email.id)

            # Affected but chair-edited → never clobber; audit that it *would*
            # have changed so the chair knows their edit was preserved.
            if (email.draft or {}).get("is_edited"):
                await audit_repo.log_action(
                    db, email_id, "ticket_redraft_skipped_edited", _ACTOR,
                    {"stored_ids": sorted(stored_ids), "fresh_ids": fresh_ids_list},
                )
                stats["skipped_edited"] += 1
                continue

            try:
                await email_repo.set_redrafting(db, email_id, True)
                await audit_repo.log_action(
                    db, email_id, "ticket_redrafting", _ACTOR,
                    {"stored_ids": sorted(stored_ids), "fresh_ids": fresh_ids_list},
                )

                classification = ClassificationResult(**(email.classification or {}))
                email_data = {
                    "from": email.sender,
                    "sender_name": email.sender_name,
                    "subject": email.subject,
                    "body": email.body,
                }

                routing = router.route(classification, fresh_chunks)
                draft = await drafter.draft(
                    email_data, classification, fresh_chunks, routing
                )
                # Same placeholder→human_review rule the orchestrator applies:
                # a draft with [CHAIR: …] gaps always needs a human.
                if draft.placeholders and routing.lane != LANE_HUMAN_REVIEW:
                    routing = routing.model_copy(
                        update={
                            "lane": LANE_HUMAN_REVIEW,
                            "override_reason": (
                                f"draft contains {len(draft.placeholders)} chair "
                                "placeholder(s) requiring input before sending"
                            ),
                        }
                    )

                before_ph = len((email.draft or {}).get("placeholders") or [])
                new_ctx = {
                    "query": ctx.get("query", ""),
                    "intent": ctx.get("intent", ""),
                    "retrieved_ids": fresh_ids_list,
                }
                await email_repo.save_redraft(
                    db, email_id,
                    draft=draft.model_dump(),
                    routing=routing.model_dump(),
                    retrieval_context=new_ctx,
                )
                await audit_repo.log_action(
                    db, email_id, "ticket_redrafted", _ACTOR,
                    {
                        "stored_ids": sorted(stored_ids),
                        "fresh_ids": fresh_ids_list,
                        "placeholders_before": before_ph,
                        "placeholders_after": len(draft.placeholders),
                        "lane": routing.lane,
                    },
                )
                stats["redrafted"] += 1
            except Exception:  # noqa: BLE001 - one bad ticket must not stop the sweep
                logger.exception("Re-draft failed for email %s; clearing flag.", email_id)
                await email_repo.set_redrafting(db, email_id, False)

    logger.info("Re-evaluation sweep complete: %s", stats)
    return stats
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_reevaluation.py -v`
Expected: PASS (all four tests). The fallback drafter makes no model call, so the "affected" tests re-draft deterministically.

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipeline/reevaluation.py backend/tests/test_reevaluation.py
git commit -m "feat(pipeline): reevaluate_open_tickets sweep (retrieval-changed gate + re-draft)"
```

---

## Task 5: Endpoint — `POST /policies/reevaluate`

**Files:**
- Modify: `backend/app/api/v1/policies.py` (new endpoint; import `BackgroundTasks` + the sweep + `EmailRepository`)
- Test: `backend/tests/test_reevaluate_endpoint.py`

**Interfaces:**
- Consumes: `reevaluate_open_tickets` (Task 4); `EmailRepository.get_open_tickets` (Task 3).
- Produces: `POST /api/v1/policies/reevaluate` → `202` with body `{"open": <n>, "scheduled": true}`. Schedules the sweep via `BackgroundTasks`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_reevaluate_endpoint.py`. Copy the in-memory `client` fixture VERBATIM from `tests/test_policies_endpoint.py` (it yields `(client, session_factory)` over a StaticPool `:memory:` DB with `get_db` overridden). **Crucially, monkeypatch the sweep** — Starlette runs `BackgroundTasks` after the response, and the *real* `reevaluate_open_tickets` would open a session on the production `async_session_factory` (a real DB file), not the test's in-memory engine. The sweep itself is fully covered by Task 4; this test verifies only the endpoint's contract: the open count + that the task is scheduled.

```python
"""POST /policies/reevaluate schedules a sweep and reports the open count."""

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from app.db.database import get_db
from app.db.models import Base, Email
from app.models.enums import EmailStatus


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as s:
            yield s

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, factory
    main.app.dependency_overrides.clear()
    await engine.dispose()


async def _seed_open(factory, n: int) -> None:
    async with factory() as s:
        for _ in range(n):
            s.add(Email(
                sender="a@b.com", subject="s", body="b",
                status=EmailStatus.DRAFT_GENERATED.value,
                retrieval_context={"query": "q", "intent": "", "retrieved_ids": []},
            ))
        await s.commit()


async def test_reevaluate_returns_open_count_and_schedules(client, monkeypatch):
    c, factory = client
    await _seed_open(factory, 3)

    ran = {}

    async def _fake_sweep(*a, **k):
        ran["called"] = True

    # The background task would otherwise hit the real DB — isolate the endpoint.
    monkeypatch.setattr("app.api.v1.policies.reevaluate_open_tickets", _fake_sweep)

    resp = await c.post("/api/v1/policies/reevaluate")
    assert resp.status_code == 202
    body = resp.json()
    assert body["open"] == 3
    assert body["scheduled"] is True
    # httpx awaits the full response incl. background tasks → the sweep ran.
    assert ran.get("called") is True


async def test_reevaluate_zero_open(client, monkeypatch):
    c, _factory = client
    monkeypatch.setattr(
        "app.api.v1.policies.reevaluate_open_tickets", lambda *a, **k: None
    )
    resp = await c.post("/api/v1/policies/reevaluate")
    assert resp.status_code == 202
    assert resp.json() == {"open": 0, "scheduled": True}
```

> `lambda *a, **k: None` in the zero-open test is fine: with no open tickets the count is 0 and the (stubbed) task is still scheduled; the stub need not be awaitable because BackgroundTasks handles sync callables too. In the first test the stub IS async so we can assert it ran.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_reevaluate_endpoint.py -v`
Expected: FAIL — `404` (route not defined).

- [ ] **Step 3: Add the endpoint**

In `backend/app/api/v1/policies.py`:

Update the FastAPI import at the top (currently `from fastapi import APIRouter, Depends, HTTPException, status`) to add `BackgroundTasks`:

```python
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
```

Add these imports below the existing repository imports:

```python
from app.pipeline.reevaluation import reevaluate_open_tickets
from app.repositories.email_repository import EmailRepository

_emails = EmailRepository()
```

Add the endpoint (place it after `list_policy_audit` and BEFORE `GET /{policy_key}`, so it is not captured as a path param):

```python
@router.post("/reevaluate", status_code=status.HTTP_202_ACCEPTED)
async def reevaluate(
    background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
) -> dict:
    """Schedule one background re-draft sweep of the open tickets.

    The chair clicks this after making a batch of KB edits. We report how many
    open tickets exist (so the UI can say "sweeping N…") and run the sweep after
    the response returns — it re-drafts only the tickets whose retrieval shifted.
    The sweep opens its own DB session (the request's session is closed by then).
    """
    open_count = len(await _emails.get_open_tickets(db))
    background_tasks.add_task(reevaluate_open_tickets)
    return {"open": open_count, "scheduled": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_reevaluate_endpoint.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole policies suite for route-ordering regressions**

Run: `cd backend && python -m pytest tests/test_policies_endpoint.py tests/test_reevaluate_endpoint.py -v`
Expected: all PASS (confirms `/reevaluate` is not shadowed by `/{policy_key}` and vice-versa).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/policies.py backend/tests/test_reevaluate_endpoint.py
git commit -m "feat(api): POST /policies/reevaluate schedules open-ticket sweep"
```

---

## Task 6: Expose `redrafting` in the email API + TS type

**Files:**
- Modify: `backend/app/api/v1/emails.py:120-136` (`_email_to_dict`)
- Modify: `frontend/src/types/index.ts` (`Email` interface)
- Test: `backend/tests/test_email_serialization_redrafting.py`

**Interfaces:**
- Consumes: `Email.redrafting`, `Email.retrieval_context`.
- Produces: `GET /emails/{id}` and `/emails/queue` rows carry `"redrafting": bool` and `"retrieval_context": dict | null`. Frontend `Email.redrafting?: boolean`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_email_serialization_redrafting.py`:

```python
"""The email serializer surfaces the redrafting flag for the live badge."""

import pytest

from app.api.v1.emails import _email_to_dict
from app.db.models import Email


def test_email_to_dict_includes_redrafting():
    e = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
    e.redrafting = True
    e.retrieval_context = {"query": "q", "intent": "", "retrieved_ids": ["policy_1"]}
    d = _email_to_dict(e)
    assert d["redrafting"] is True
    assert d["retrieval_context"]["retrieved_ids"] == ["policy_1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_email_serialization_redrafting.py -v`
Expected: FAIL — `KeyError: 'redrafting'`.

- [ ] **Step 3: Add the fields to `_email_to_dict`**

In `backend/app/api/v1/emails.py`, in `_email_to_dict` (after the `"draft": email.draft,` line), add:

```python
        "redrafting": bool(email.redrafting),
        "retrieval_context": email.retrieval_context,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_email_serialization_redrafting.py -v`
Expected: PASS.

- [ ] **Step 5: Add the field to the frontend `Email` type**

In `frontend/src/types/index.ts`, in the `Email` interface (after the `draft: DraftResult | null;` line, ~line 184), add:

```typescript
  /**
   * Transient re-evaluation state: true while a KB-change sweep is re-drafting
   * this ticket. Drives the "re-drafting…" badge; cleared when the new draft
   * lands (pushed live over the /emails/stream SSE).
   */
  redrafting?: boolean;
```

- [ ] **Step 6: Verify tsc**

Run: `cd frontend && npx tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/v1/emails.py backend/tests/test_email_serialization_redrafting.py frontend/src/types/index.ts
git commit -m "feat(api): expose redrafting + retrieval_context on email rows"
```

---

## Task 7: Frontend API client + hook

**Files:**
- Modify: `frontend/src/lib/api/policies.ts` (`reevaluatePolicies`)
- Modify: `frontend/src/hooks/usePolicies.ts` (`useReevaluatePolicies`)

**Interfaces:**
- Consumes: `POST /policies/reevaluate` (Task 5).
- Produces:
  - `reevaluatePolicies(): Promise<{ open: number; scheduled: boolean }>`.
  - `useReevaluatePolicies()` — a mutation returning that payload.

- [ ] **Step 1: Add the API client function**

In `frontend/src/lib/api/policies.ts`, mirror the existing functions' style (they use `apiClient`). Add:

```typescript
/** Response of POST /policies/reevaluate. */
export interface ReevaluateResponse {
  open: number;
  scheduled: boolean;
}

/**
 * Trigger one background re-draft sweep of the open tickets after KB edits.
 * Returns immediately with the open-ticket count; the sweep runs server-side.
 */
export async function reevaluatePolicies(): Promise<ReevaluateResponse> {
  const { data } = await apiClient.post<ReevaluateResponse>("/policies/reevaluate");
  return data;
}
```

> Match the import/return idiom already used in this file — if the other functions destructure `apiClient.get`/`.post` differently (e.g. return `res.data` via a shared helper), follow that exact pattern instead.

- [ ] **Step 2: Add the hook**

In `frontend/src/hooks/usePolicies.ts`, add `reevaluatePolicies` to the existing `@/lib/api` import list, then add:

```typescript
/**
 * Trigger a re-draft sweep of open tickets. On success, invalidate the email
 * queue so any tickets flipping into "re-drafting…" (and their new drafts) show
 * up — the SSE stream also pushes these, this is the immediate nudge.
 */
export function useReevaluatePolicies() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => reevaluatePolicies(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["emailQueue"] });
    },
  });
}
```

`useMutation`, `useQueryClient` are already imported at the top of the file.

- [ ] **Step 3: Verify tsc**

Run: `cd frontend && npx tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api/policies.ts frontend/src/hooks/usePolicies.ts
git commit -m "feat(fe): reevaluatePolicies client + useReevaluatePolicies hook"
```

---

## Task 8: KB page — "Re-evaluate open tickets" button

**Files:**
- Modify: `frontend/src/app/knowledge-base/page.tsx`

**Interfaces:**
- Consumes: `useReevaluatePolicies` (Task 7).
- Produces: a button in the Policies view header that triggers the sweep and shows a transient "Sweeping N open tickets…" confirmation.

- [ ] **Step 1: Wire the hook + a small result banner**

In `frontend/src/app/knowledge-base/page.tsx`:

Add to the hooks import (line 6, `from "@/hooks"`): include `useReevaluatePolicies`.

Inside the component, near the other mutations (after `reactivateMutation`), add:

```tsx
  const reevaluate = useReevaluatePolicies();
```

In the Policies view, replace the single-button header row (the `<div className="flex justify-end">` that holds the "Add internal policy" button) with a row that also carries the re-evaluate button and its result note:

```tsx
          <div className="flex items-center justify-end gap-3">
            {reevaluate.isSuccess && (
              <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
                {reevaluate.data.open === 0
                  ? "No open tickets to re-evaluate."
                  : `Re-evaluating ${reevaluate.data.open} open ticket${
                      reevaluate.data.open === 1 ? "" : "s"
                    }…`}
              </span>
            )}
            <Button
              type="button"
              variant="secondary"
              onClick={() => reevaluate.mutate()}
              disabled={reevaluate.isPending}
            >
              {reevaluate.isPending ? "Starting…" : "Re-evaluate open tickets"}
            </Button>
            <Button type="button" onClick={() => setAddOpen((v) => !v)}>
              <Plus className="h-4 w-4" />
              Add internal policy
            </Button>
          </div>
```

> If `Button` has no `variant="secondary"`, check `components/ui` for the available variants and use the closest neutral one (or omit `variant`). Do not invent a variant.

- [ ] **Step 2: Verify tsc**

Run: `cd frontend && npx tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/knowledge-base/page.tsx
git commit -m "feat(fe): re-evaluate open tickets button on the KB page"
```

---

## Task 9: "re-drafting…" badge in the queue + detail

**Files:**
- Modify: `frontend/src/components/email/EmailListItem.tsx`
- Modify: `frontend/src/components/email/EmailDetail.tsx`

**Interfaces:**
- Consumes: `Email.redrafting` (Task 6). The live refresh already works: the sweep writes `ticket_redrafting` / `ticket_redrafted` audit rows → SSE → `useEmailQueueStream` invalidates `emailQueue` → rows refetch with the new `redrafting` value.
- Produces: a "re-drafting…" badge on a re-drafting row and a banner in the detail pane.

- [ ] **Step 1: Add the badge to the list item**

In `frontend/src/components/email/EmailListItem.tsx`, in the right-hand badge column (the `<span className="flex shrink-0 flex-col items-end gap-1.5">` group that holds the lane `<Badge>`), add — as the first child so it sits on top — a redrafting badge:

```tsx
        {email.redrafting && (
          <Badge variant="warning" size="sm">
            re-drafting…
          </Badge>
        )}
```

> `Badge` is already imported at the top of the file. If `warning` is not a valid `Badge` variant, use whichever variant the codebase uses for an in-progress/attention state (check `laneBadgeVariant` / the `Badge` component for the variant union) — do not invent one.

- [ ] **Step 2: Add a banner to the detail pane**

In `frontend/src/components/email/EmailDetail.tsx`, near the top of the rendered detail (above the draft editor / body), add a conditional banner:

```tsx
      {email.redrafting && (
        <div
          className="mb-3 rounded-md px-3 py-2 text-sm"
          style={{
            backgroundColor: "var(--accent-subtle)",
            color: "var(--accent)",
          }}
        >
          This ticket is being re-drafted after a knowledge-base change…
        </div>
      )}
```

> Place it inside the detail's scroll container, before the draft section. Match the surrounding JSX indentation and confirm `email` is the prop name in scope (it is, per `queue/page.tsx` passing `email={selectedEmail}`).

- [ ] **Step 3: Verify tsc**

Run: `cd frontend && npx tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/email/EmailListItem.tsx frontend/src/components/email/EmailDetail.tsx
git commit -m "feat(fe): re-drafting badge in queue row + detail banner"
```

---

## Task 10: Full-suite gate + docs

**Files:**
- Modify: `backend/CLAUDE.md`? No — the project CLAUDE.md is at repo root; update per the session rule.
- Modify: `docs/REEVALUATE_ON_POLICY_CHANGE_DESIGN.md` status line (DRAFT → IMPLEMENTED).

- [ ] **Step 1: Run the full backend suite (fast half)**

Run: `cd backend && python -m pytest tests/ -m "not ml" -q`
Expected: all PASS (the 186 pre-existing + the new re-eval tests). If anything fails, fix before proceeding — no red tests at the gate.

- [ ] **Step 2: Run the frontend type gate**

Run: `cd frontend && npx tsc --noEmit`
Expected: 0 errors.

- [ ] **Step 3: Flip the design status line**

In `docs/REEVALUATE_ON_POLICY_CHANGE_DESIGN.md`, change the status line (line 3) from `Status: **DRAFT for review**` to `Status: **IMPLEMENTED** (2026-07-19)`.

- [ ] **Step 4: Commit**

```bash
git add docs/REEVALUATE_ON_POLICY_CHANGE_DESIGN.md
git commit -m "docs: mark re-evaluate-on-policy-change design implemented"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- §2 Decision A (overwrite in place, skip chair-edited) → Task 4 (`save_redraft` overwrite; `is_edited` skip + `ticket_redraft_skipped_edited` audit). ✅
- §2 Decision B (transient `redrafting`, SSE-live) → Task 1 (column), Task 4 (set/clear + audit rows that publish SSE), Task 6 (serialize), Task 9 (badge). ✅
- §2 Decision C (button, one sweep) → Task 5 (endpoint schedules ONE sweep), Task 8 (button). ✅
- §2 Decision D + §3 (all change kinds via retrieval-changed gate) → Task 4 (`set(fresh) != set(stored)`, kind-agnostic). ✅
- §3 gate free (no model call) → Task 2 persists query+intent; Task 4 gate is pure `retriever.retrieve`. ✅
- §4.1 persist retrieval context → Task 2. ✅
- §4.2 endpoint → Task 5. §4.3 sweep → Task 4. §4.4 idempotency/concurrency → Task 4 (`if email.redrafting: continue`; repeat-sweep no-op test). ✅
- §5 schema (two columns) → Task 1. §6 components → Tasks 2/4/3/5/6/8/9. §7 testing → tests in Tasks 1-5. ✅
- §8 out-of-scope (no preview, no auto-trigger, no re-classify) → honored: sweep reuses stored classification, re-routes only. ✅

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N" — every code step carries full code. ✅

**Type consistency:** `get_open_tickets` / `set_redrafting` / `save_redraft` names identical across Tasks 3-5. `retrieval_context` shape `{query,intent,retrieved_ids}` identical across Tasks 1/2/4/6. `reevaluate_open_tickets(session_factory=…) -> dict` signature identical across Tasks 4-5. `ReevaluateResponse {open, scheduled}` matches the endpoint's `{"open", "scheduled": True}` (Task 5) — note the endpoint returns `open` count + `scheduled`, NOT the sweep's full stats (the sweep runs after the response). ✅

**Note on a deliberate spec refinement:** the design doc's `retrieval_context` says `{"queries":[...], "retrieved_ids":[...]}`. The plan stores `{"query": <str>, "intent": <str>, "retrieved_ids":[...]}` — the *exact* retriever inputs — so the gate reproduces retrieval bit-for-bit with zero re-join/re-derivation. Functionally identical intent, strictly more faithful. Flag this to the reviewer at merge.
