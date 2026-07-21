"""Versioning/lineage tests: model defaults + PATCH /edit + POST /revert-edit.

Same throwaway-in-memory-SQLite harness as test_policies_endpoint.py.
"""
import httpx
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


async def test_new_policy_lineage_defaults(client):
    _, factory = client
    async with factory() as s:
        s.add(PolicyDocument(policy_key="policy_1", title="t", content="c"))
        await s.commit()
        row = (await s.execute(select(PolicyDocument).where(PolicyDocument.policy_key == "policy_1"))).scalar_one()
        assert row.version == 1
        assert row.supersedes is None
        assert row.superseded_by is None
        assert row.root_key is None


from app.repositories.policy_repository import PolicyRepository


async def test_repo_edit_policy_creates_version_and_retires_base(client):
    _, factory = client
    repo = PolicyRepository()
    async with factory() as s:
        s.add(PolicyDocument(policy_key="policy_10", title="Old", content="old body",
                             visibility="public", status="active"))
        await s.commit()
        base = await repo.get_by_key(s, "policy_10")
        new = await repo.edit_policy(s, base=base, title="New", content="new body",
                                     category=None, visibility="public", actor="Chair1")
        assert new.policy_key == "policy_10__v2"
        assert new.status == "active" and new.version == 2
        assert new.supersedes == "policy_10" and new.root_key == "policy_10"
        assert new.source == "chair:Chair1"
        base_after = await repo.get_by_key(s, "policy_10")
        assert base_after.status == "inactive"
        assert base_after.superseded_by == "policy_10__v2"


async def test_repo_revert_edit_restores_ancestor(client):
    _, factory = client
    repo = PolicyRepository()
    async with factory() as s:
        s.add(PolicyDocument(policy_key="policy_20", title="Old", content="old body",
                             visibility="public", status="active"))
        await s.commit()
        base = await repo.get_by_key(s, "policy_20")
        tip = await repo.edit_policy(s, base=base, title="New", content="new body",
                                     category=None, visibility="public", actor="Chair1")
        restored = await repo.revert_edit(s, tip=tip)
        assert restored.policy_key == "policy_20" and restored.status == "active"
        assert restored.superseded_by is None
        tip_after = await repo.get_by_key(s, "policy_20__v2")
        assert tip_after.status == "inactive"


async def test_edit_endpoint_versions_and_audits(client):
    c, factory = client
    async with factory() as s:
        s.add(PolicyDocument(policy_key="policy_30", title="Old title", content="a b c",
                             visibility="public", status="active"))
        await s.commit()
    resp = await c.patch("/api/v1/policies/policy_30/edit", json={
        "title": "Old title", "content": "a b' c", "visibility": "public", "actor": "Chair1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["policy_key"] == "policy_30__v2"
    assert body["version"] == 2 and body["supersedes"] == "policy_30"
    async with factory() as s:
        base = (await s.execute(select(PolicyDocument).where(PolicyDocument.policy_key == "policy_30"))).scalar_one()
        assert base.status == "inactive" and base.superseded_by == "policy_30__v2"
        edited = [a for a in (await s.execute(select(PolicyAuditLog))).scalars().all() if a.action == "policy_edited"]
        assert len(edited) == 1
        assert edited[0].before["content"] == "a b c"
        assert edited[0].after["content"] == "a b' c"


async def test_edit_preserves_visibility_when_omitted(client):
    c, factory = client
    async with factory() as s:
        s.add(PolicyDocument(policy_key="int_x", title="t", content="c",
                             visibility="internal", status="active"))
        await s.commit()
    resp = await c.patch("/api/v1/policies/int_x/edit", json={
        "title": "t", "content": "c2", "actor": "Chair1"})
    assert resp.status_code == 200
    assert resp.json()["visibility"] == "internal"


async def test_edit_rejects_stale_updated_at(client):
    c, factory = client
    async with factory() as s:
        s.add(PolicyDocument(policy_key="policy_40", title="t", content="c",
                             visibility="public", status="active"))
        await s.commit()
    resp = await c.patch("/api/v1/policies/policy_40/edit", json={
        "title": "t", "content": "c2", "actor": "Chair1",
        "expected_updated_at": "1999-01-01T00:00:00+00:00"})
    assert resp.status_code == 409


async def test_edit_non_active_tip_409(client):
    c, factory = client
    async with factory() as s:
        s.add(PolicyDocument(policy_key="policy_50", title="t", content="c",
                             visibility="public", status="inactive"))
        await s.commit()
    resp = await c.patch("/api/v1/policies/policy_50/edit", json={
        "title": "t", "content": "c2", "actor": "Chair1"})
    assert resp.status_code == 409


async def test_edit_unknown_404(client):
    c, _ = client
    resp = await c.patch("/api/v1/policies/nope/edit", json={
        "title": "t", "content": "c", "actor": "Chair1"})
    assert resp.status_code == 404


async def test_revert_edit_endpoint_restores_and_audits(client):
    c, factory = client
    async with factory() as s:
        s.add(PolicyDocument(policy_key="policy_60", title="t", content="orig",
                             visibility="public", status="active"))
        await s.commit()
    edited = await c.patch("/api/v1/policies/policy_60/edit", json={
        "title": "t", "content": "changed", "actor": "Chair1"})
    tip_key = edited.json()["policy_key"]
    resp = await c.post(f"/api/v1/policies/{tip_key}/revert-edit", json={"actor": "Chair1"})
    assert resp.status_code == 200
    assert resp.json()["policy_key"] == "policy_60" and resp.json()["status"] == "active"
    async with factory() as s:
        base = (await s.execute(select(PolicyDocument).where(PolicyDocument.policy_key == "policy_60"))).scalar_one()
        assert base.status == "active" and base.superseded_by is None
        tip = (await s.execute(select(PolicyDocument).where(PolicyDocument.policy_key == tip_key))).scalar_one()
        assert tip.status == "inactive"
        reverted = [a for a in (await s.execute(select(PolicyAuditLog))).scalars().all() if a.action == "policy_edit_reverted"]
        assert len(reverted) == 1 and reverted[0].policy_key == tip_key


async def test_revert_edit_non_tip_409(client):
    c, factory = client
    async with factory() as s:
        s.add(PolicyDocument(policy_key="policy_70", title="t", content="c",
                             visibility="public", status="active"))
        await s.commit()
    # original with no supersedes → not revertable
    resp = await c.post("/api/v1/policies/policy_70/revert-edit", json={"actor": "Chair1"})
    assert resp.status_code == 409
