"""Endpoint tests for the policies API (v1): citation-detail read + KB governance.

Exercises the real router against a throwaway in-memory SQLite DB (StaticPool +
overridden ``get_db`` + httpx ASGITransport). The ``client`` fixture yields
``(client, session_factory)`` so each test seeds exactly the rows it needs and can
inspect the DB directly. No real DB file, no network.
"""

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


# --- citation-detail read endpoint (GET /policies/{key}) --------------------
async def test_get_policy_returns_full_chunk(client):
    c, factory = client
    async with factory() as s:
        s.add(PolicyDocument(
            policy_key="policy_117",
            title="Camera-Ready Deadline",
            content="The camera-ready deadline is specified in AoE time.",
            category="submission_deadlines",
            score=1.0,
            # [tags-dropped E007] tags=["deadline", "camera-ready", "aoe"],
            source="AAAI-27 call_for_papers.md",
        ))
        await s.commit()
    resp = await c.get("/api/v1/policies/policy_117")
    assert resp.status_code == 200
    body = resp.json()
    # [tags-dropped E007] "tags" removed from the response shape.
    assert set(body) == {"policy_key", "title", "content", "category", "source", "score"}
    assert body["policy_key"] == "policy_117"
    assert body["title"] == "Camera-Ready Deadline"
    assert body["content"].startswith("The camera-ready deadline")
    assert body["category"] == "submission_deadlines"
    # [tags-dropped E007] assert body["tags"] == ["deadline", "camera-ready", "aoe"]
    assert body["source"] == "AAAI-27 call_for_papers.md"


@pytest.mark.skip(reason="[tags-dropped E007] tags column dropped; null-coercion no longer applies")
async def test_get_policy_coerces_null_tags_to_list(client):
    c, factory = client
    async with factory() as s:
        s.add(PolicyDocument(
            policy_key="policy_150",
            title="Reviewer Assignment",
            content="Reviewers are assigned by the program committee.",
            category="review_process",
            score=None,
            tags=None,
            source="AAAI-27 reviewer_guidelines.md",
        ))
        await s.commit()
    resp = await c.get("/api/v1/policies/policy_150")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tags"] == []
    assert body["score"] is None
    assert body["source"] == "AAAI-27 reviewer_guidelines.md"


async def test_get_policy_unknown_key_404(client):
    c, _ = client
    resp = await c.get("/api/v1/policies/policy_999")
    assert resp.status_code == 404
    assert "policy_999" in resp.json()["detail"]


# --- KB governance endpoints (create / retire / reactivate / list / audit) --
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


async def test_retire_is_idempotent_and_audits_only_once(client):
    c, factory = client
    create_resp = await c.post("/api/v1/policies", json={
        "title": "Idempotent retire test", "content": "content here", "actor": "1"})
    assert create_resp.status_code == 201
    policy_key = create_resp.json()["policy_key"]

    resp1 = await c.patch(f"/api/v1/policies/{policy_key}/retire", json={"actor": "1"})
    assert resp1.status_code == 200
    assert resp1.json() == {"policy_key": policy_key, "status": "inactive"}

    async with factory() as s:
        audit_rows = (
            await s.execute(
                select(PolicyAuditLog).where(
                    PolicyAuditLog.policy_key == policy_key,
                    PolicyAuditLog.action == "policy_retired",
                )
            )
        ).scalars().all()
        assert len(audit_rows) == 1
        assert audit_rows[0].before == {"status": "active"}

    resp2 = await c.patch(f"/api/v1/policies/{policy_key}/retire", json={"actor": "1"})
    assert resp2.status_code == 200
    assert resp2.json() == {"policy_key": policy_key, "status": "inactive"}

    async with factory() as s:
        audit_rows = (
            await s.execute(
                select(PolicyAuditLog).where(
                    PolicyAuditLog.policy_key == policy_key,
                    PolicyAuditLog.action == "policy_retired",
                )
            )
        ).scalars().all()
        assert len(audit_rows) == 1

        row = (
            await s.execute(select(PolicyDocument).where(PolicyDocument.policy_key == policy_key))
        ).scalar_one()
        assert row.status == "inactive"


async def test_create_with_retire_keys_supersedes_and_audits(client):
    c, factory = client

    async with factory() as s:
        s.add(PolicyDocument(
            policy_key="policy_200",
            title="Active policy",
            content="Old content",
            visibility="public",
            status="active",
        ))
        s.add(PolicyDocument(
            policy_key="policy_201",
            title="Another active policy",
            content="More content",
            visibility="public",
            status="active",
        ))
        s.add(PolicyDocument(
            policy_key="policy_202",
            title="Inactive policy",
            content="Already inactive",
            visibility="public",
            status="inactive",
        ))
        await s.commit()

    resp = await c.post("/api/v1/policies", json={
        "title": "New ruling",
        "content": "New content",
        "actor": "1",
        "retire_keys": ["policy_200", "policy_202", "missing_key"],
    })
    assert resp.status_code == 201
    new_key = resp.json()["policy_key"]

    async with factory() as s:
        policy_200 = (
            await s.execute(select(PolicyDocument).where(PolicyDocument.policy_key == "policy_200"))
        ).scalar_one()
        assert policy_200.status == "inactive"

        policy_202 = (
            await s.execute(select(PolicyDocument).where(PolicyDocument.policy_key == "policy_202"))
        ).scalar_one()
        assert policy_202.status == "inactive"

        audit_rows = (await s.execute(select(PolicyAuditLog))).scalars().all()
        policy_created = [a for a in audit_rows if a.action == "policy_created"]
        policy_retired = [a for a in audit_rows if a.action == "policy_retired"]

        assert len(policy_created) == 1
        assert policy_created[0].policy_key == new_key

        assert len(policy_retired) == 1
        assert policy_retired[0].policy_key == "policy_200"
        assert policy_retired[0].before == {"status": "active"}


async def test_list_policies_filters(client):
    c, factory = client
    async with factory() as s:
        s.add_all([
            PolicyDocument(policy_key="policy_1", title="Deadline", content="x", visibility="public", status="active"),
            PolicyDocument(policy_key="int_a", title="Ruling", content="y", visibility="internal", status="inactive"),
        ])
        await s.commit()
    r = await c.get("/api/v1/policies", params={"visibility": "public"})
    assert r.status_code == 200
    keys = [p["policy_key"] for p in r.json()["policies"]]
    assert keys == ["policy_1"]


async def test_reactivate_missing_and_success_and_noop(client):
    c, factory = client
    async with factory() as s:
        s.add(PolicyDocument(policy_key="int_b", title="t", content="c", visibility="internal", status="inactive"))
        await s.commit()
    assert (await c.patch("/api/v1/policies/nope/reactivate", json={"actor": "Chair1"})).status_code == 404
    ok = await c.patch("/api/v1/policies/int_b/reactivate", json={"actor": "Chair1"})
    assert ok.status_code == 200 and ok.json()["status"] == "active"
    async with factory() as s:
        acts = [a.action for a in (await s.execute(select(PolicyAuditLog))).scalars().all()]
    assert acts.count("policy_reactivated") == 1
    # second call: already active → no-op, no new audit row
    await c.patch("/api/v1/policies/int_b/reactivate", json={"actor": "Chair1"})
    async with factory() as s:
        acts2 = [a.action for a in (await s.execute(select(PolicyAuditLog))).scalars().all()]
    assert acts2.count("policy_reactivated") == 1


async def test_policy_audit_endpoint(client):
    c, factory = client
    await c.post("/api/v1/policies", json={"title": "New", "content": "z", "actor": "Chair1"})
    r = await c.get("/api/v1/policies/audit")
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert any(e["action"] == "policy_created" for e in entries)
    assert "timestamp" in entries[0]
