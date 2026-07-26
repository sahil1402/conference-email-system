"""Email detail hydrates ``retrieved_chunks`` from ``retrieval_context`` (2a).

``retrieved_chunks`` is not a stored column: the pipeline persists only the
rank-ordered ``retrieval_context.retrieved_ids``, and the detail endpoints join
those against ``policy_documents`` on read. These tests pin the read-time
contract (rank order, null/empty handling, resilience) and — via the endpoint
tests — the deliberate absence of a status filter, so a retired or superseded
policy still resolves to what actually grounded the draft.

Distinct from ``draft.citations`` (what the model claims it cited).
"""

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from app.api.v1 import emails as emails_api
from app.db.database import get_db
from app.db.models import Base, Email, PolicyDocument


def _email(ctx):
    e = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
    e.id = 1
    e.retrieval_context = ctx
    return e


class _Row:
    """Minimal stand-in for a PolicyDocument row."""

    def __init__(self, key, title="T", content="C", category="cat"):
        self.policy_key = key
        self.title = title
        self.content = content
        self.category = category


# ---------------------------------------------------------------------------
# _hydrate_retrieved_chunks — unit level (policy lookup stubbed)
# ---------------------------------------------------------------------------
async def test_hydrates_in_retrieval_rank_order(monkeypatch):
    """Output order follows retrieved_ids, NOT the DB's return order."""
    ids = ["policy_186", "policy_172", "policy_171"]

    async def fake_get_by_keys(db, keys):
        assert set(keys) == set(ids)  # one bulk call, all ids at once
        # Deliberately returned in a different order than requested.
        return {
            "policy_171": _Row("policy_171", "Third", "c3", "submission"),
            "policy_186": _Row("policy_186", "First", "c1", "modification"),
            "policy_172": _Row("policy_172", "Second", "c2", "submission"),
        }

    monkeypatch.setattr(emails_api.policy_repo, "get_by_keys", fake_get_by_keys)

    chunks = await emails_api._hydrate_retrieved_chunks(
        None, _email({"query": "q", "retrieved_ids": ids})
    )

    assert [c["policy_id"] for c in chunks] == ids
    assert chunks[0] == {
        "policy_id": "policy_186",
        "title": "First",
        "content": "c1",
        "category": "modification",
    }
    # score is never fabricated — it was never persisted.
    assert all("score" not in c for c in chunks)


async def test_null_retrieval_context_returns_none(monkeypatch):
    """Legacy / never-processed row: absent grounding set, not an empty one."""

    async def boom(db, keys):  # must not even be consulted
        raise AssertionError("should not query policies for a null context")

    monkeypatch.setattr(emails_api.policy_repo, "get_by_keys", boom)
    assert await emails_api._hydrate_retrieved_chunks(None, _email(None)) is None


async def test_empty_retrieved_ids_returns_empty_list(monkeypatch):
    """A real query that matched nothing → [] (distinct from null context)."""

    async def boom(db, keys):
        raise AssertionError("should not query policies for an empty id list")

    monkeypatch.setattr(emails_api.policy_repo, "get_by_keys", boom)
    out = await emails_api._hydrate_retrieved_chunks(
        None, _email({"query": "q", "retrieved_ids": []})
    )
    assert out == []


async def test_missing_policy_row_is_skipped_not_fatal(monkeypatch):
    """An id with no row is dropped; the rest still resolve."""

    async def fake_get_by_keys(db, keys):
        return {"policy_186": _Row("policy_186")}

    monkeypatch.setattr(emails_api.policy_repo, "get_by_keys", fake_get_by_keys)
    out = await emails_api._hydrate_retrieved_chunks(
        None, _email({"retrieved_ids": ["policy_186", "policy_gone"]})
    )
    assert [c["policy_id"] for c in out] == ["policy_186"]


async def test_lookup_failure_degrades_to_none(monkeypatch):
    """A DB error must not 500 an email the chair is trying to read."""

    async def explode(db, keys):
        raise RuntimeError("db down")

    monkeypatch.setattr(emails_api.policy_repo, "get_by_keys", explode)
    out = await emails_api._hydrate_retrieved_chunks(
        None, _email({"retrieved_ids": ["policy_186"]})
    )
    assert out is None


# ---------------------------------------------------------------------------
# End-to-end through the real endpoint + real repository query
# ---------------------------------------------------------------------------
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


async def _seed(factory, *, ctx, policies):
    async with factory() as s:
        for p in policies:
            s.add(p)
        e = Email(sender="a@b.com", subject="s", body="b", status="draft_generated")
        e.retrieval_context = ctx
        s.add(e)
        await s.commit()
        return str(e.id)


async def test_detail_endpoint_serves_populated_chunks(client):
    c, factory = client
    email_id = await _seed(
        factory,
        ctx={"query": "withdraw", "retrieved_ids": ["policy_186", "policy_171"]},
        policies=[
            PolicyDocument(policy_key="policy_186", title="Modification Guidelines",
                           content="After the deadline…", category="submission",
                           visibility="public", status="active"),
            PolicyDocument(policy_key="policy_171", title="Abstract Submission",
                           content="Abstracts are due…", category="submission",
                           visibility="public", status="active"),
        ],
    )

    r = await c.get(f"/api/v1/emails/{email_id}")
    assert r.status_code == 200
    chunks = r.json()["email"]["retrieved_chunks"]
    assert [ch["policy_id"] for ch in chunks] == ["policy_186", "policy_171"]
    assert chunks[0]["title"] == "Modification Guidelines"
    assert chunks[0]["content"] == "After the deadline…"
    assert chunks[1]["category"] == "submission"


async def test_detail_endpoint_null_context_serves_null(client):
    c, factory = client
    email_id = await _seed(factory, ctx=None, policies=[])

    r = await c.get(f"/api/v1/emails/{email_id}")
    assert r.status_code == 200
    assert r.json()["email"]["retrieved_chunks"] is None


async def test_retired_and_superseded_policies_still_resolve(client):
    """The grounding set is HISTORY: a policy retired or superseded after the
    draft was generated must still resolve, or citations would silently vanish
    from the review UI whenever a chair edits the KB."""
    c, factory = client
    email_id = await _seed(
        factory,
        ctx={"retrieved_ids": ["int_x", "int_y"]},
        policies=[
            # Retired after the fact.
            PolicyDocument(policy_key="int_x", title="Retired One", content="old text",
                           category="c", visibility="internal", status="inactive"),
            # Superseded by a __v2 edit (edit_policy leaves the base row in place).
            PolicyDocument(policy_key="int_y", title="Superseded One", content="v1 text",
                           category="c", visibility="internal", status="inactive",
                           superseded_by="int_y__v2", root_key="int_y", version=1),
        ],
    )

    r = await c.get(f"/api/v1/emails/{email_id}")
    assert r.status_code == 200
    chunks = r.json()["email"]["retrieved_chunks"]
    assert [ch["policy_id"] for ch in chunks] == ["int_x", "int_y"]
    # The SUPERSEDED text is served — what actually grounded this draft.
    assert chunks[1]["content"] == "v1 text"


async def test_queue_list_is_not_hydrated(client):
    """The queue serializer stays untouched (no N+1 across the page)."""
    c, factory = client
    await _seed(
        factory,
        ctx={"retrieved_ids": ["policy_186"]},
        policies=[PolicyDocument(policy_key="policy_186", title="T", content="C",
                                 category="c", visibility="public", status="active")],
    )

    r = await c.get("/api/v1/emails/queue")
    assert r.status_code == 200
    rows = r.json()["emails"]
    assert rows and all("retrieved_chunks" not in row for row in rows)
