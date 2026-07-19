# Layered Knowledge Base — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `policy_documents` into a layered, DB-backed knowledge base: a public (scraped) layer plus a chair-authored internal overlay, with visibility/status filtering applied once in the repository so every retriever inherits it.

**Architecture:** Add `visibility` + `status` columns to `policy_documents`; add a single filtered read (`PolicyRepository.list_for_index`) that both BM25 and FAISS use; make the public seed idempotent (upsert-by-key, content-only); add chair authoring (`create_internal`/`retire`) behind a `/api/v1/policies` endpoint that audits to a new `policy_audit_logs` table and rebuilds the index. Distill + fusion retrieval strategy is unchanged.

**Tech Stack:** Python 3.12, FastAPI, async SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Alembic (async, single `DATABASE_URL`), rank_bm25, FAISS, pytest + pytest-asyncio.

## Global Constraints

- Spec of record: `docs/LAYERED_KB_DESIGN.md`. Decisions: two tiers `public`+`internal` (both retrievable/citable); current-truth + audit (no version chains / date columns); retriever reads the DB.
- DB access only through repositories — never raw SQL in the pipeline (architecture rule 5).
- No AI vendor/model names in source or docs.
- Dialect-agnostic JSON access only (`Col["key"].as_string()`), never `func.json_extract` — must pass on SQLite (dev) and Postgres (prod).
- No Claude attribution trailers in commit messages.
- Tests are hermetic (in-memory SQLite via `create_async_engine(...StaticPool)` + `Base.metadata.create_all`); the autouse conftest fixture forces `MODEL_PROVIDER=fallback`.
- Run tests from `backend/`: `cd backend && python -m pytest ...`. Fast suite excludes `-m ml`.
- `policy_key` is UNIQUE — overrides are retire-plus-add, never two rows per key.

---

### Task 1: Add `visibility` + `status` columns to `policy_documents`

**Files:**
- Modify: `backend/app/db/models.py` (PolicyDocument, after `source` at ~line 137)
- Create: `backend/migrations/versions/f1a2b3c4d5e6_phase_f_policy_kb_layers.py`
- Test: `backend/tests/test_policy_kb_layers.py`

**Interfaces:**
- Produces: `PolicyDocument.visibility: str` (default `"public"`), `PolicyDocument.status: str` (default `"active"`), both indexed, non-null.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_policy_kb_layers.py
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, PolicyDocument


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


async def test_policy_document_defaults_public_active(session):
    row = PolicyDocument(policy_key="policy_101", title="T", content="C")
    session.add(row)
    await session.commit()
    await session.refresh(row)
    assert row.visibility == "public"
    assert row.status == "active"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_policy_kb_layers.py::test_policy_document_defaults_public_active -v`
Expected: FAIL — `AttributeError`/`TypeError` (no `visibility`/`status` attribute).

- [ ] **Step 3: Add the columns to the model**

In `backend/app/db/models.py`, inside `class PolicyDocument`, immediately after the `source` column (~line 137), add:

```python
    # Phase F (layered KB): the layer/trust axis and the soft on/off lifecycle.
    # visibility: "public" (official corpus, freely citable) | "internal"
    # (chair-authored, not on the public site — retrievable & citable, marked for
    # provenance). status: "active" (indexed) | "inactive" (retired, not indexed).
    # Both DB + Python defaults so raw migrations and ORM inserts agree.
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default="public", server_default="public", index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active", index=True
    )
```

- [ ] **Step 4: Create the migration**

```python
# backend/migrations/versions/f1a2b3c4d5e6_phase_f_policy_kb_layers.py
"""phase_f_policy_kb_layers

Adds ``visibility`` (public|internal) and ``status`` (active|inactive) to
``policy_documents`` for the layered KB. Server defaults backfill every existing
row to public/active. Schema only.

Revision ID: f1a2b3c4d5e6
Revises: b8d3f6a1c204
Create Date: 2026-07-18 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "b8d3f6a1c204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("policy_documents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("visibility", sa.String(length=16), nullable=False, server_default="public")
        )
        batch_op.add_column(
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active")
        )
        batch_op.create_index("ix_policy_documents_visibility", ["visibility"])
        batch_op.create_index("ix_policy_documents_status", ["status"])


def downgrade() -> None:
    with op.batch_alter_table("policy_documents", schema=None) as batch_op:
        batch_op.drop_index("ix_policy_documents_status")
        batch_op.drop_index("ix_policy_documents_visibility")
        batch_op.drop_column("status")
        batch_op.drop_column("visibility")
```

- [ ] **Step 5: Run tests + confirm migration applies**

Run: `cd backend && python -m pytest tests/test_policy_kb_layers.py -v && alembic upgrade head`
Expected: test PASSES; `alembic upgrade head` runs clean to `f1a2b3c4d5e6`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/db/models.py backend/migrations/versions/f1a2b3c4d5e6_phase_f_policy_kb_layers.py backend/tests/test_policy_kb_layers.py
git commit -m "feat(kb): add visibility + status columns to policy_documents"
```

---

### Task 2: `PolicyRepository.list_for_index` + new-column passthrough

**Files:**
- Modify: `backend/app/repositories/policy_repository.py`
- Test: `backend/tests/test_policy_kb_layers.py` (append)

**Interfaces:**
- Consumes: `PolicyDocument.visibility`, `PolicyDocument.status` (Task 1).
- Produces: `PolicyRepository.list_for_index(db, visibilities=("public", "internal")) -> list[PolicyDocument]` — active rows whose visibility is in the set, ordered by id. `_POLICY_COLUMNS` now includes `visibility`, `status`.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_policy_kb_layers.py
from app.repositories.policy_repository import PolicyRepository


async def test_list_for_index_filters_status_and_visibility(session):
    repo = PolicyRepository()
    session.add_all([
        PolicyDocument(policy_key="policy_101", title="pub", content="c", visibility="public", status="active"),
        PolicyDocument(policy_key="int_x", title="int", content="c", visibility="internal", status="active"),
        PolicyDocument(policy_key="policy_102", title="off", content="c", visibility="public", status="inactive"),
    ])
    await session.commit()

    keys = {p.policy_key for p in await repo.list_for_index(session)}
    assert keys == {"policy_101", "int_x"}                       # inactive excluded

    pub_only = await repo.list_for_index(session, visibilities=("public",))
    assert {p.policy_key for p in pub_only} == {"policy_101"}    # internal excluded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_policy_kb_layers.py::test_list_for_index_filters_status_and_visibility -v`
Expected: FAIL — `AttributeError: 'PolicyRepository' object has no attribute 'list_for_index'`.

- [ ] **Step 3: Implement `list_for_index` + column passthrough**

In `backend/app/repositories/policy_repository.py`: extend `_POLICY_COLUMNS` to include the new columns, and add the method to `PolicyRepository`.

```python
_POLICY_COLUMNS = {
    "policy_key", "title", "content", "category", "score", "tags", "source",
    "visibility", "status",
}
```

```python
    async def list_for_index(
        self,
        db: AsyncSession,
        visibilities: tuple[str, ...] = ("public", "internal"),
    ) -> list[PolicyDocument]:
        """Return active policies whose visibility is in ``visibilities``.

        This is the single corpus query every retriever indexes, so the
        visibility/status filter lives in exactly one place.
        """
        result = await db.execute(
            select(PolicyDocument)
            .where(PolicyDocument.status == "active")
            .where(PolicyDocument.visibility.in_(visibilities))
            .order_by(PolicyDocument.id)
        )
        return list(result.scalars().all())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_policy_kb_layers.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/policy_repository.py backend/tests/test_policy_kb_layers.py
git commit -m "feat(kb): add PolicyRepository.list_for_index filtered corpus query"
```

---

### Task 3: BM25 retriever reads the DB (via `list_for_index`)

**Files:**
- Modify: `backend/app/pipeline/retriever.py` (class `PolicyRetriever`)
- Test: `backend/tests/test_retriever_db.py`

**Interfaces:**
- Consumes: `PolicyRepository.list_for_index` (Task 2); `async_session_factory` from `app.db.database`.
- Produces: `PolicyRetriever(policy_repo=None, session_factory=None)` — now DB-backed; `retrieve` and `rebuild_index` unchanged in signature; `_ensure_loaded` is now `async`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_retriever_db.py
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, PolicyDocument
from app.pipeline.retriever import PolicyRetriever


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with f() as s:
        s.add_all([
            PolicyDocument(policy_key="policy_101", title="Submission deadline",
                           content="Papers must be submitted by the deadline.",
                           visibility="public", status="active"),
            PolicyDocument(policy_key="int_ext", title="Deadline extension",
                           content="The submission deadline has been extended.",
                           visibility="internal", status="active"),
            PolicyDocument(policy_key="policy_102", title="Retired rule",
                           content="deadline old removed", visibility="public", status="inactive"),
        ])
        await s.commit()
    yield f
    await engine.dispose()


async def test_bm25_retrieves_active_public_and_internal_excludes_inactive(factory):
    r = PolicyRetriever(session_factory=factory)
    hits = await r.retrieve("submission deadline extended", intent="submission_deadline", top_k=5)
    keys = {h.policy_id for h in hits}
    assert "int_ext" in keys           # internal is retrievable
    assert "policy_102" not in keys    # inactive is excluded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_retriever_db.py -v`
Expected: FAIL — `PolicyRetriever.__init__` takes no `session_factory` (still file-backed).

- [ ] **Step 3: Rewrite `PolicyRetriever` to load from the DB**

In `backend/app/pipeline/retriever.py`, replace the `PolicyRetriever.__init__`, `_ensure_loaded`, `rebuild_index`, `document_count`, and `retrieve` load-site to mirror the FAISS pattern. Replace the class body (from `def __init__` through the end of `retrieve`) with:

```python
    def __init__(
        self,
        backend: str = "bm25",
        policy_repo=None,
        session_factory=None,
    ) -> None:
        from app.db.database import async_session_factory
        from app.repositories.policy_repository import PolicyRepository

        self.backend = backend
        self._policy_repo = policy_repo or PolicyRepository()
        self._session_factory = session_factory or async_session_factory
        # Cached on first retrieve(); cleared by rebuild_index().
        self._policies: list[dict] | None = None
        self._index: BM25Okapi | None = None

    async def _ensure_loaded(self) -> None:
        """Load the active corpus from the DB and build the BM25 index (once)."""
        if self._index is not None and self._policies is not None:
            return
        async with self._session_factory() as db:
            rows = await self._policy_repo.list_for_index(db)
        self._policies = [
            {
                "id": r.policy_key or "",
                "title": r.title or "",
                "content": r.content or "",
                "category": r.category or "",
                "tags": r.tags or [],
            }
            for r in rows
        ]
        corpus = [
            _tokenize(f"{p['title']} {p['content']} {' '.join(p['tags'])}")
            for p in self._policies
        ]
        # rank_bm25 requires a non-empty corpus; guard the empty-KB case.
        self._index = BM25Okapi(corpus) if corpus else None

    def rebuild_index(self) -> None:
        """Clear the cache so the next retrieve() reloads from the DB."""
        self._policies = None
        self._index = None

    @property
    def document_count(self) -> int:
        """Number of documents currently indexed (0 until first load)."""
        return len(self._policies or [])

    async def retrieve(
        self, query: str, intent: str, top_k: int = 3
    ) -> list[RetrievedChunk]:
        """Return up to ``top_k`` active policy chunks most relevant to the query."""
        await self._ensure_loaded()
        if not self._policies or self._index is None:
            return []

        query_tokens = _tokenize(f"{query} {intent}")
        scores = self._index.get_scores(query_tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        positive = [i for i in ranked if scores[i] > 0]
        chosen = (positive or ranked)[:top_k]

        return [
            RetrievedChunk(
                policy_id=self._policies[i]["id"],
                title=self._policies[i]["title"],
                content=self._policies[i]["content"],
                score=float(scores[i]),
                category=self._policies[i]["category"],
                tags=self._policies[i]["tags"],
            )
            for i in chosen
        ]
```

Then delete the now-unused file-loading constants `_DEFAULT_KB_PATH` and the `import json` / `from pathlib import Path` lines at the top of the file (they are no longer referenced).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_retriever_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipeline/retriever.py backend/tests/test_retriever_db.py
git commit -m "feat(kb): BM25 retriever reads the DB corpus via list_for_index"
```

---

### Task 4: FAISS retriever inherits the filter

**Files:**
- Modify: `backend/app/pipeline/faiss_retriever.py:65` (`_load_policies`)
- Test: `backend/tests/test_retriever_db.py` (append)

**Interfaces:**
- Consumes: `PolicyRepository.list_for_index` (Task 2).
- Produces: FAISS now indexes the same active/visibility-filtered set as BM25.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_retriever_db.py
from app.pipeline.faiss_retriever import FAISSRetriever


@pytest.mark.ml
async def test_faiss_excludes_inactive(factory):
    r = FAISSRetriever(session_factory=factory)
    await r.build()
    keys = {d["policy_id"] for d in r._docs}
    assert "int_ext" in keys
    assert "policy_102" not in keys   # inactive excluded via list_for_index
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_retriever_db.py::test_faiss_excludes_inactive -v`
Expected: FAIL — inactive `policy_102` present (FAISS still calls `get_all_policies`).

- [ ] **Step 3: Switch FAISS to `list_for_index`**

In `backend/app/pipeline/faiss_retriever.py`, `_load_policies` (line 62-65):

```python
    async def _load_policies(self) -> list:
        """Fetch the active, visibility-filtered corpus in a short-lived session."""
        async with self._session_factory() as db:
            return await self._policy_repo.list_for_index(db)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_retriever_db.py::test_faiss_excludes_inactive -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipeline/faiss_retriever.py backend/tests/test_retriever_db.py
git commit -m "feat(kb): FAISS retriever inherits active/visibility filter"
```

---

### Task 5: Idempotent public importer (`upsert_by_key` + seed rewrite)

**Files:**
- Modify: `backend/app/repositories/policy_repository.py`
- Modify: `backend/scripts/seed_real_policies.py`
- Test: `backend/tests/test_policy_kb_layers.py` (append)

**Interfaces:**
- Consumes: `_map_policy`, `list_for_index`.
- Produces: `PolicyRepository.upsert_by_key(db, raw: dict, *, source: str) -> str` returning `"inserted"` or `"updated"`. Updates only content fields (`title`, `content`, `category`, `tags`); never `status`/`visibility`.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_policy_kb_layers.py
async def test_upsert_by_key_updates_content_but_preserves_governance_fields(session):
    repo = PolicyRepository()

    assert await repo.upsert_by_key(session, {"id": "policy_101", "title": "v1", "content": "a"}, source="aaai_scrape") == "inserted"

    # a chair retires it (governance field)
    row = (await repo.list_for_index(session, visibilities=("public",)))[0]
    row.status = "inactive"
    await session.commit()

    # re-scrape changes the content
    assert await repo.upsert_by_key(session, {"id": "policy_101", "title": "v2", "content": "b"}, source="aaai_scrape") == "updated"

    from sqlalchemy import select
    from app.db.models import PolicyDocument
    got = (await session.execute(select(PolicyDocument).where(PolicyDocument.policy_key == "policy_101"))).scalar_one()
    assert got.title == "v2"            # content refreshed
    assert got.status == "inactive"     # governance field NOT resurrected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_policy_kb_layers.py::test_upsert_by_key_updates_content_but_preserves_governance_fields -v`
Expected: FAIL — no `upsert_by_key`.

- [ ] **Step 3: Implement `upsert_by_key`**

Add to `PolicyRepository` in `backend/app/repositories/policy_repository.py`:

```python
    # Content fields the importer owns. status/visibility are chair-owned and are
    # never written on update (prevents a re-scrape resurrecting a retired policy).
    _IMPORTER_FIELDS = ("title", "content", "category", "tags")

    async def upsert_by_key(self, db: AsyncSession, raw: dict, *, source: str) -> str:
        """Insert a new public policy or refresh an existing one's content.

        Returns "inserted" or "updated". On update, only content fields change;
        status/visibility are left as-is.
        """
        mapped = _map_policy(raw)
        key = mapped.get("policy_key")
        if not key:
            raise ValueError("policy dict needs 'policy_key' or 'id'")

        existing = (
            await db.execute(select(PolicyDocument).where(PolicyDocument.policy_key == key))
        ).scalar_one_or_none()

        if existing is None:
            # Strip keys we set explicitly — real policies.json rows carry
            # their own "source" (and mapped may carry visibility/status), which
            # would collide with the explicit kwargs below (TypeError).
            content = {k: v for k, v in mapped.items() if k not in ("source", "visibility", "status")}
            db.add(PolicyDocument(visibility="public", status="active", source=source, **content))
            await db.commit()
            return "inserted"

        for field in self._IMPORTER_FIELDS:
            if field in mapped:
                setattr(existing, field, mapped[field])
        existing.source = source
        await db.commit()
        return "updated"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_policy_kb_layers.py::test_upsert_by_key_updates_content_but_preserves_governance_fields -v`
Expected: PASS.

- [ ] **Step 5: Rewrite the seed script to be idempotent**

Replace the body of `main()` in `backend/scripts/seed_real_policies.py` (lines 27-36) with:

```python
async def main() -> None:
    policies = json.loads(_POLICIES_PATH.read_text(encoding="utf-8"))
    repo = PolicyRepository()
    inserted = updated = 0
    async with async_session_factory() as db:
        for p in policies:
            outcome = await repo.upsert_by_key(db, p, source="aaai_scrape")
            inserted += outcome == "inserted"
            updated += outcome == "updated"
    print(f"Public layer synced from {_POLICIES_PATH.name}: {inserted} inserted, {updated} updated.")
```

- [ ] **Step 6: Run + verify idempotency manually**

Run: `cd backend && python -m pytest tests/test_policy_kb_layers.py -v`
Expected: PASS. (Seed script is exercised against the real DB in Task 9's verification, not here.)

- [ ] **Step 7: Commit**

```bash
git add backend/app/repositories/policy_repository.py backend/scripts/seed_real_policies.py backend/tests/test_policy_kb_layers.py
git commit -m "feat(kb): idempotent public importer (upsert_by_key, content-only)"
```

---

### Task 6: `policy_audit_logs` table + `PolicyAuditRepository`

**Files:**
- Modify: `backend/app/db/models.py` (new `PolicyAuditLog` after `PolicyDocument`)
- Create: `backend/migrations/versions/a7b8c9d0e1f2_phase_f_policy_audit.py`
- Create: `backend/app/repositories/policy_audit_repository.py`
- Test: `backend/tests/test_policy_audit.py`

**Interfaces:**
- Produces: `PolicyAuditLog(id, policy_key, action, actor, before JSON, after JSON, timestamp)`; `PolicyAuditRepository.log(db, *, policy_key, action, actor, before=None, after=None) -> PolicyAuditLog`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_policy_audit.py
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, PolicyAuditLog
from app.repositories.policy_audit_repository import PolicyAuditRepository


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with f() as s:
        yield s
    await engine.dispose()


async def test_policy_audit_log_persists(session):
    repo = PolicyAuditRepository()
    entry = await repo.log(session, policy_key="int_x", action="policy_created",
                           actor="chair:1", before=None, after={"title": "T"})
    assert entry.id is not None
    rows = (await session.execute(select(PolicyAuditLog))).scalars().all()
    assert len(rows) == 1
    assert rows[0].action == "policy_created"
    assert rows[0].after == {"title": "T"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_policy_audit.py -v`
Expected: FAIL — `ImportError` (`PolicyAuditLog` / `PolicyAuditRepository` do not exist).

- [ ] **Step 3: Add the model**

In `backend/app/db/models.py`, after `class PolicyDocument`, add:

```python
class PolicyAuditLog(Base):
    """Append-only record of KB governance actions (create / edit / retire)."""

    __tablename__ = "policy_audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    policy_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

- [ ] **Step 4: Create the migration**

```python
# backend/migrations/versions/a7b8c9d0e1f2_phase_f_policy_audit.py
"""phase_f_policy_audit

Adds the ``policy_audit_logs`` table — append-only KB governance history
(create/edit/retire), separate from the email-scoped ``audit_logs``.

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-07-18 00:10:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "policy_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("policy_key", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_policy_audit_logs_policy_key", "policy_audit_logs", ["policy_key"])


def downgrade() -> None:
    op.drop_index("ix_policy_audit_logs_policy_key", table_name="policy_audit_logs")
    op.drop_table("policy_audit_logs")
```

- [ ] **Step 5: Add the repository**

```python
# backend/app/repositories/policy_audit_repository.py
"""Append-only persistence for KB governance actions (policy_audit_logs)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PolicyAuditLog


class PolicyAuditRepository:
    """Async writer for the ``policy_audit_logs`` table."""

    async def log(
        self,
        db: AsyncSession,
        *,
        policy_key: str,
        action: str,
        actor: str,
        before: dict | None = None,
        after: dict | None = None,
    ) -> PolicyAuditLog:
        """Append one governance entry and return it."""
        entry = PolicyAuditLog(
            policy_key=policy_key, action=action, actor=actor, before=before, after=after
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return entry
```

- [ ] **Step 6: Run tests + migration**

Run: `cd backend && python -m pytest tests/test_policy_audit.py -v && alembic upgrade head`
Expected: PASS; migration clean to `a7b8c9d0e1f2`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/db/models.py backend/migrations/versions/a7b8c9d0e1f2_phase_f_policy_audit.py backend/app/repositories/policy_audit_repository.py backend/tests/test_policy_audit.py
git commit -m "feat(kb): policy_audit_logs table + PolicyAuditRepository"
```

---

### Task 7: `create_internal` + `retire` repository methods

**Files:**
- Modify: `backend/app/repositories/policy_repository.py`
- Test: `backend/tests/test_policy_kb_layers.py` (append)

**Interfaces:**
- Produces:
  - `PolicyRepository.create_internal(db, *, title, content, category=None, tags=None, actor) -> PolicyDocument` — generates `int_<slug>` (counter on collision), inserts `visibility="internal"`, `status="active"`, `source=f"chair:{actor}"`.
  - `PolicyRepository.retire(db, policy_key) -> PolicyDocument | None` — sets `status="inactive"`; returns the row or None if absent.
  - `PolicyRepository.get_by_key(db, policy_key) -> PolicyDocument | None`.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_policy_kb_layers.py
async def test_create_internal_and_retire(session):
    repo = PolicyRepository()

    row = await repo.create_internal(session, title="Deadline Extended!", content="now March 5", actor="1")
    assert row.policy_key == "int_deadline-extended"
    assert row.visibility == "internal" and row.status == "active"
    assert row.source == "chair:1"

    dup = await repo.create_internal(session, title="Deadline Extended!", content="again", actor="1")
    assert dup.policy_key == "int_deadline-extended-2"     # collision → counter

    retired = await repo.retire(session, "int_deadline-extended")
    assert retired.status == "inactive"
    assert await repo.retire(session, "does_not_exist") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_policy_kb_layers.py::test_create_internal_and_retire -v`
Expected: FAIL — no `create_internal`.

- [ ] **Step 3: Implement the methods**

Add to `PolicyRepository` (and `import re` at the top of the file if not present):

```python
    @staticmethod
    def _slugify(text: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
        return slug or "policy"

    async def get_by_key(self, db: AsyncSession, policy_key: str) -> PolicyDocument | None:
        return (
            await db.execute(select(PolicyDocument).where(PolicyDocument.policy_key == policy_key))
        ).scalar_one_or_none()

    async def create_internal(
        self,
        db: AsyncSession,
        *,
        title: str,
        content: str,
        category: str | None = None,
        tags: list | None = None,
        actor: str,
    ) -> PolicyDocument:
        """Insert a chair-authored internal policy with a generated unique key."""
        base = f"int_{self._slugify(title)}"
        key, n = base, 1
        while await self.get_by_key(db, key) is not None:
            n += 1
            key = f"{base}-{n}"
        row = PolicyDocument(
            policy_key=key, title=title, content=content, category=category,
            tags=tags or [], source=f"chair:{actor}", visibility="internal", status="active",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    async def retire(self, db: AsyncSession, policy_key: str) -> PolicyDocument | None:
        """Soft-retire a policy (status='inactive'). Returns the row or None."""
        row = await self.get_by_key(db, policy_key)
        if row is None:
            return None
        row.status = "inactive"
        await db.commit()
        await db.refresh(row)
        return row
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_policy_kb_layers.py::test_create_internal_and_retire -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/policy_repository.py backend/tests/test_policy_kb_layers.py
git commit -m "feat(kb): create_internal + retire repository methods"
```

---

### Task 8: `/api/v1/policies` endpoints (create + retire + similar)

**Files:**
- Create: `backend/app/api/v1/policies.py`
- Modify: `backend/app/pipeline/fusion_retriever.py` (add `rebuild_index`)
- Modify: `backend/main.py` (import + `include_router`)
- Test: `backend/tests/test_policies_endpoint.py`

**Interfaces:**
- Consumes: `PolicyRepository.create_internal/retire` (Task 7), `PolicyAuditRepository.log` (Task 6), `get_retriever()` (for similar), `get_db`.
- Produces: `FusionRetriever.rebuild_index()` (async) — clears both wrapped rankers.
- Produces:
  - `POST /api/v1/policies` body `{title, content, category?, tags?, actor, retire_keys?}` → creates internal row, retires each `retire_keys` entry, audits each action, calls `get_retriever().rebuild_index()`. Returns `{policy_key, visibility, status}`.
  - `PATCH /api/v1/policies/{policy_key}/retire` body `{actor}` → 404 if absent, else retire + audit + rebuild; returns `{policy_key, status}`.
  - `POST /api/v1/policies/similar` body `{title, content}` → `{"similar": [{policy_key, title, score}]}` from `get_retriever().retrieve(...)`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_policies_endpoint.py
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from app.db.database import get_db
from app.db.models import Base, PolicyAuditLog, PolicyDocument


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
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


async def test_create_internal_policy_writes_row_and_audit(client):
    c, factory = client
    resp = await c.post("/api/v1/policies", json={
        "title": "Deadline extended", "content": "now March 5", "actor": "1"})
    assert resp.status_code == 201
    assert resp.json()["visibility"] == "internal"

    async with factory() as s:
        rows = (await s.execute(select(PolicyDocument))).scalars().all()
        assert len(rows) == 1 and rows[0].status == "active"
        audit = (await s.execute(select(PolicyAuditLog))).scalars().all()
        assert len(audit) == 1 and audit[0].action == "policy_created"


async def test_retire_missing_returns_404(client):
    c, _ = client
    resp = await c.patch("/api/v1/policies/nope/retire", json={"actor": "1"})
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_policies_endpoint.py -v`
Expected: FAIL — 404 route not found (router not mounted).

- [ ] **Step 3: Create the router**

```python
# backend/app/api/v1/policies.py
"""KB governance API (v1) — chair authoring of the internal policy overlay.

Mounted under /api/v1 → paths are /api/v1/policies*. Every mutation writes a
policy_audit_logs entry and rebuilds the retriever index so the live KB reflects
the change with no restart.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.pipeline.retriever import get_retriever
from app.repositories.policy_audit_repository import PolicyAuditRepository
from app.repositories.policy_repository import PolicyRepository

router = APIRouter(prefix="/policies", tags=["policies"])

_policies = PolicyRepository()
_audit = PolicyAuditRepository()


class CreatePolicyRequest(BaseModel):
    title: str
    content: str
    category: str | None = None
    tags: list[str] | None = None
    actor: str
    retire_keys: list[str] = []


class RetireRequest(BaseModel):
    actor: str


class SimilarRequest(BaseModel):
    title: str
    content: str


async def _rebuild_index() -> None:
    """Clear the active retriever's cache so the next retrieve() reloads the KB.

    BM25's rebuild_index is sync (returns None); FAISS's and Fusion's are async
    (return a coroutine). Handle both without assuming which backend is active.
    """
    import inspect

    result = get_retriever().rebuild_index()
    if inspect.isawaitable(result):
        await result


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_policy(payload: CreatePolicyRequest, db: AsyncSession = Depends(get_db)) -> dict:
    row = await _policies.create_internal(
        db, title=payload.title, content=payload.content,
        category=payload.category, tags=payload.tags, actor=payload.actor,
    )
    await _audit.log(db, policy_key=row.policy_key, action="policy_created",
                     actor=f"chair:{payload.actor}", after={"title": row.title})
    for key in payload.retire_keys:
        retired = await _policies.retire(db, key)
        if retired is not None:
            await _audit.log(db, policy_key=key, action="policy_retired",
                             actor=f"chair:{payload.actor}", before={"status": "active"},
                             after={"status": "inactive", "superseded_by": row.policy_key})
    await _rebuild_index()
    return {"policy_key": row.policy_key, "visibility": row.visibility, "status": row.status}


@router.patch("/{policy_key}/retire")
async def retire_policy(policy_key: str, payload: RetireRequest, db: AsyncSession = Depends(get_db)) -> dict:
    row = await _policies.retire(db, policy_key)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"policy {policy_key} not found")
    await _audit.log(db, policy_key=policy_key, action="policy_retired",
                     actor=f"chair:{payload.actor}", before={"status": "active"},
                     after={"status": "inactive"})
    await _rebuild_index()
    return {"policy_key": policy_key, "status": row.status}


@router.post("/similar")
async def similar_policies(payload: SimilarRequest) -> dict:
    hits = await get_retriever().retrieve(f"{payload.title} {payload.content}", intent="", top_k=5)
    return {"similar": [{"policy_key": h.policy_id, "title": h.title, "score": h.score} for h in hits]}
```

- [ ] **Step 4: Add `rebuild_index` to `FusionRetriever`**

`FusionRetriever` (production default) has no `rebuild_index`; `_rebuild_index()`
would `AttributeError`. Add to `backend/app/pipeline/fusion_retriever.py` inside
`class FusionRetriever`:

```python
    async def rebuild_index(self) -> None:
        """Clear both wrapped rankers so the next retrieve() reloads the KB."""
        self.bm25.rebuild_index()          # BM25 clear is synchronous
        await self.faiss.rebuild_index()   # FAISS re-encode is async
```

- [ ] **Step 5: Mount the router**

In `backend/main.py`, next to the other v1 imports (`from app.api.v1.retrieval import router as retrieval_router`) add:

```python
from app.api.v1.policies import router as policies_router
```

and next to the other `app.include_router(..., prefix="/api/v1")` calls, add:

```python
app.include_router(policies_router, prefix="/api/v1")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_policies_endpoint.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/v1/policies.py backend/app/pipeline/fusion_retriever.py backend/main.py backend/tests/test_policies_endpoint.py
git commit -m "feat(kb): /api/v1/policies endpoints (create, retire, similar) with audit + reindex"
```

---

### Task 9: Scrub internal keys from requester-facing text

**Files:**
- Modify: `backend/app/pipeline/drafter.py:38` (`_CITATION_PATTERN`) and `:94` (`_INLINE_ID_RE`)
- Test: `backend/tests/test_drafter_local.py` (append)

**Interfaces:**
- Consumes: nothing new — extends existing scrub regexes to also match `int_<slug>` keys.

- [ ] **Step 1: Write the failing test**

First locate the reply-cleaning function that applies `_INLINE_ID_RE` (search `_INLINE_ID_RE.sub` in `drafter.py`) — call it `<clean_fn>` in the test below.

```python
# append to backend/tests/test_drafter_local.py
from app.pipeline import drafter as _d


def test_internal_keys_scrubbed_from_reply():
    text = "Per the deadline policy (int_deadline-extended), you may proceed."
    cleaned = _d._INLINE_ID_RE.sub("", text)
    assert "int_deadline-extended" not in cleaned
    # existing behavior still holds
    assert "policy_" not in _d._INLINE_ID_RE.sub("", "see policy_101 here")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_drafter_local.py::test_internal_keys_scrubbed_from_reply -v`
Expected: FAIL — `int_deadline-extended` survives (regex only matches `policy_\d+`).

- [ ] **Step 3: Extend the regexes**

In `backend/app/pipeline/drafter.py`, replace the two patterns:

```python
# line 38
_CITATION_PATTERN = re.compile(r"(?:policy_\d+|int_[a-z0-9-]+)")
```

```python
# lines 94-96
_INLINE_ID_RE = re.compile(
    r"\s*\((?:see\s+)?(?:policy_\d+|int_[a-z0-9-]+)"
    r"(?:\s*,\s*(?:policy_\d+|int_[a-z0-9-]+))*\)"
    r"|\b(?:policy_\d+|int_[a-z0-9-]+)\b"
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_drafter_local.py::test_internal_keys_scrubbed_from_reply -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/pipeline/drafter.py backend/tests/test_drafter_local.py
git commit -m "feat(kb): scrub internal (int_) policy keys from requester text"
```

---

### Task 10: Full-suite gate + live KB verification

**Files:**
- Modify: `backend/tests/test_retrieval_backend_info.py` or wherever fusion/backends are asserted, only if a test hard-codes the file-backed corpus count (fix if red).

- [ ] **Step 1: Run the fast suite**

Run: `cd backend && python -m pytest -m "not ml" -q`
Expected: all pass. If any test fails because it assumed the file-backed BM25 corpus, update it to seed the DB via the in-memory fixture (pattern in `tests/test_retriever_db.py`).

- [ ] **Step 2: Run the ml suite**

Run: `cd backend && python -m pytest -m ml -q`
Expected: all pass (FAISS tests included).

- [ ] **Step 3: Live verification against the real DB**

```bash
cd backend && alembic upgrade head && python scripts/seed_real_policies.py
# expect: "Public layer synced ...: N inserted, M updated." (93 rows public/active)
python - <<'PY'
import asyncio
from app.db.database import async_session_factory
from app.repositories.policy_repository import PolicyRepository
async def go():
    repo = PolicyRepository()
    async with async_session_factory() as db:
        rows = await repo.list_for_index(db)
        print("active corpus:", len(rows), "| visibilities:", {r.visibility for r in rows})
asyncio.run(go())
PY
```
Expected: `active corpus: 93 | visibilities: {'public'}`.

- [ ] **Step 4: End-to-end governance smoke (app running)**

Launch backend, then:
```bash
curl -s -X POST localhost:8000/api/v1/policies -H 'content-type: application/json' \
  -d '{"title":"Deadline extended","content":"The submission deadline is now March 5.","actor":"1"}'
curl -s -X POST localhost:8000/api/v1/policies/similar -H 'content-type: application/json' \
  -d '{"title":"deadline","content":"when is the submission deadline"}'
```
Expected: create returns `visibility":"internal"`; similar returns the new internal policy among the hits (proves it entered the live index via rebuild).

- [ ] **Step 5: Commit any test fixes**

```bash
git add -A
git commit -m "test(kb): green full suite on DB-backed layered corpus"
```

---

## Self-Review

- **Spec coverage:** §3.1 schema→T1; §3.2 repository→T2,T5,T7; §3.3 BM25→DB→T3; §3.4 importer→T5; §3.5 endpoints→T8; §3.6 governance (similar/retire/audit)→T6,T7,T8; §4 retrieval unchanged→T3/T4 (filter upstream, strategy untouched); §5 citation/scrub→T9; §7 testing→every task + T10. Postgres compat (§6): no `func.json_extract` introduced; all queries use ORM comparisons.
- **Types:** `list_for_index(db, visibilities)` consistent T2/T3/T4/T5; `upsert_by_key(db, raw, *, source)->str` T5; `create_internal(...)->PolicyDocument` / `retire(...)->PolicyDocument|None` T7/T8; `PolicyAuditRepository.log(db, *, policy_key, action, actor, before, after)` T6/T8.
- **Placeholders:** none — every code step carries real code.
- **Known follow-ups (out of scope):** `MAX_RETRIEVED_CHUNKS` 3→5 and score-floor/dynamic-k (retrieval tuning); frontend UI for chair authoring (this plan is API-only); auth wiring for `actor` (endpoints take `actor` in the body until the account system lands).
