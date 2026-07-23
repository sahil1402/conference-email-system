"""Endpoint tests for conflict detection wiring (2e).

The retriever + LLM detector are mocked at the policies-module boundary, so
these tests are deterministic and never touch the real KB DB or a model.
"""
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from app.api.v1 import policies as policies_mod
from app.db.database import get_db
from app.db.models import Base, PolicyDocument
from app.pipeline.policy_conflict import ConflictItem, ConflictReport


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


class _FakeChunk:
    def __init__(self, policy_id, title, content, score=0.5):
        self.policy_id, self.title, self.content, self.score = policy_id, title, content, score
        self.category, self.tags, self.intents = None, [], []


class _FakeRetriever:
    def __init__(self, chunks):
        self._chunks = chunks

    async def retrieve(self, query, intent="", top_k=10, **kw):
        return self._chunks[:top_k]

    def rebuild_index(self):
        return None


def _install(monkeypatch, chunks, detect):
    """Patch the retriever + detector the policies module calls."""
    monkeypatch.setattr(policies_mod, "get_retriever", lambda: _FakeRetriever(chunks))
    monkeypatch.setattr(policies_mod, "detect_conflicts", detect)


def _canned_detect(calls):
    """A detect_conflicts double that flags the first candidate as a conflict."""
    async def _detect(*, title, content, candidates):
        calls.append({"title": title, "n": len(candidates)})
        keys = [c["policy_key"] for c in candidates]
        conflicts = (
            [ConflictItem(policy_key=keys[0], title=candidates[0]["title"],
                          explanation="mock conflict", snippets=[])]
            if keys else []
        )
        return ConflictReport(
            checked_at="2026-07-23T00:00:00Z", available=True,
            summary=f"{len(conflicts)} of {len(keys)} related policies conflict.",
            candidates_checked=keys, conflicts=conflicts,
        )
    return _detect


_CHUNKS = [
    _FakeChunk("other_1", "Existing A", "A content"),
    _FakeChunk("other_2", "Existing B", "B content"),
]


async def test_similar_returns_conflict_report(client, monkeypatch):
    c, _ = client
    _install(monkeypatch, _CHUNKS, _canned_detect([]))
    r = await c.post("/api/v1/policies/similar", json={"title": "New", "content": "x"})
    assert r.status_code == 200
    body = r.json()
    assert [s["policy_key"] for s in body["similar"]] == ["other_1", "other_2"]
    rep = body["conflict_report"]
    assert rep["available"] is True
    assert [cf["policy_key"] for cf in rep["conflicts"]] == ["other_1"]


async def test_create_computes_and_persists_report(client, monkeypatch):
    c, factory = client
    calls = []
    _install(monkeypatch, _CHUNKS, _canned_detect(calls))
    r = await c.post("/api/v1/policies", json={"title": "New ruling", "content": "y", "actor": "1"})
    assert r.status_code == 201
    assert len(calls) == 1  # computed server-side
    assert [cf["policy_key"] for cf in r.json()["conflict_report"]["conflicts"]] == ["other_1"]
    async with factory() as s:
        row = (await s.execute(select(PolicyDocument))).scalar_one()
        assert row.conflict_report["conflicts"][0]["policy_key"] == "other_1"


async def test_create_reuses_precomputed_report_without_second_call(client, monkeypatch):
    c, factory = client
    calls = []
    _install(monkeypatch, _CHUNKS, _canned_detect(calls))
    precomputed = ConflictReport(
        checked_at="2026-07-23T00:00:00Z", available=True,
        summary="1 of 1 related policies conflict.", candidates_checked=["pre_1"],
        conflicts=[ConflictItem(policy_key="pre_1", title="Pre", explanation="e", snippets=[])],
    ).model_dump()
    r = await c.post("/api/v1/policies", json={
        "title": "New", "content": "y", "actor": "1", "conflict_report": precomputed})
    assert r.status_code == 201
    assert calls == []  # the model was NOT called again
    assert r.json()["conflict_report"]["conflicts"][0]["policy_key"] == "pre_1"
    async with factory() as s:
        row = (await s.execute(select(PolicyDocument))).scalar_one()
        assert row.conflict_report["conflicts"][0]["policy_key"] == "pre_1"


async def test_create_recomputes_on_invalid_precomputed_report(client, monkeypatch):
    c, _ = client
    calls = []
    _install(monkeypatch, _CHUNKS, _canned_detect(calls))
    r = await c.post("/api/v1/policies", json={
        "title": "New", "content": "y", "actor": "1", "conflict_report": {"garbage": 1}})
    assert r.status_code == 201
    assert len(calls) == 1  # invalid report → recomputed
    assert [cf["policy_key"] for cf in r.json()["conflict_report"]["conflicts"]] == ["other_1"]


async def test_recheck_endpoint_updates_report(client, monkeypatch):
    c, factory = client
    async with factory() as s:
        s.add(PolicyDocument(policy_key="int_x", title="t", content="c",
                             visibility="internal", status="active"))
        await s.commit()
    calls = []
    _install(monkeypatch, _CHUNKS, _canned_detect(calls))
    r = await c.post("/api/v1/policies/int_x/recheck")
    assert r.status_code == 200 and len(calls) == 1
    assert r.json()["conflict_report"]["conflicts"][0]["policy_key"] == "other_1"
    async with factory() as s:
        row = (await s.execute(select(PolicyDocument).where(PolicyDocument.policy_key == "int_x"))).scalar_one()
        assert row.conflict_report["conflicts"][0]["policy_key"] == "other_1"


async def test_recheck_missing_404(client, monkeypatch):
    c, _ = client
    _install(monkeypatch, _CHUNKS, _canned_detect([]))
    assert (await c.post("/api/v1/policies/nope/recheck")).status_code == 404


async def test_reactivate_refreshes_report(client, monkeypatch):
    c, factory = client
    async with factory() as s:
        s.add(PolicyDocument(policy_key="int_r", title="t", content="c",
                             visibility="internal", status="inactive"))
        await s.commit()
    _install(monkeypatch, _CHUNKS, _canned_detect([]))
    r = await c.patch("/api/v1/policies/int_r/reactivate", json={"actor": "1"})
    assert r.status_code == 200
    assert r.json()["conflict_report"]["conflicts"][0]["policy_key"] == "other_1"


async def test_list_has_conflicts_filter(client, monkeypatch):
    c, factory = client
    report = {
        "checked_at": "2026-07-23T00:00:00Z", "available": True,
        "summary": "1 of 1 related policies conflict.",
        "candidates_checked": ["policy_b"],
        "conflicts": [{"policy_key": "policy_b", "title": "B", "explanation": "x", "snippets": []}],
    }
    async with factory() as s:
        # int_a conflicts with an active policy_b → kept by the filter.
        s.add(PolicyDocument(policy_key="int_a", title="A", content="a",
                             visibility="internal", status="active", conflict_report=report))
        s.add(PolicyDocument(policy_key="policy_b", title="B", content="b",
                             visibility="public", status="active"))
        # int_clean was checked but has no conflicts → excluded.
        s.add(PolicyDocument(policy_key="int_clean", title="Clean", content="c",
                             visibility="internal", status="active",
                             conflict_report={"checked_at": "2026-07-23T00:00:00Z", "available": True,
                                              "summary": "No conflicts.", "candidates_checked": [], "conflicts": []}))
        # int_never was never checked → excluded.
        s.add(PolicyDocument(policy_key="int_never", title="Never", content="n",
                             visibility="internal", status="active"))
        await s.commit()
    r = await c.get("/api/v1/policies", params={"status": "active", "has_conflicts": "true"})
    assert r.status_code == 200
    keys = [p["policy_key"] for p in r.json()["policies"]]
    assert keys == ["int_a"]  # only the policy with a live conflict


async def test_list_prunes_stale_conflicts(client, monkeypatch):
    c, factory = client
    stale_report = {
        "checked_at": "2026-07-23T00:00:00Z", "available": True,
        "summary": "2 of 2 related policies conflict.",
        "candidates_checked": ["policy_b", "policy_c"],
        "conflicts": [
            {"policy_key": "policy_b", "title": "B", "explanation": "x", "snippets": []},
            {"policy_key": "policy_c", "title": "C", "explanation": "y", "snippets": []},
        ],
    }
    async with factory() as s:
        s.add(PolicyDocument(policy_key="int_a", title="A", content="a",
                             visibility="internal", status="active", conflict_report=stale_report))
        s.add(PolicyDocument(policy_key="policy_b", title="B", content="b",
                             visibility="public", status="active"))
        s.add(PolicyDocument(policy_key="policy_c", title="C", content="c",
                             visibility="public", status="inactive"))  # retired → stale ref
        await s.commit()
    r = await c.get("/api/v1/policies", params={"status": "active"})
    assert r.status_code == 200
    a = next(p for p in r.json()["policies"] if p["policy_key"] == "int_a")
    keys = [cf["policy_key"] for cf in a["conflict_report"]["conflicts"]]
    assert keys == ["policy_b"]  # policy_c dropped (now inactive)
    assert a["conflict_report"]["summary"] == "1 of 2 related policies conflict."
