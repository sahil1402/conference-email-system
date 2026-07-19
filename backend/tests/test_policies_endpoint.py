"""Endpoint tests for the read-only policy-detail API (GET /api/v1/policies/{key}).

Exercises the real router against a throwaway in-memory SQLite DB (StaticPool +
overridden ``get_db`` + httpx ASGITransport), mirroring test_audit_endpoint.py.
Backs the citation-detail popup: a cited policy id must resolve to its full
chunk (source, tags, body); a missing id must 404. No real DB file, no network.
"""

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import main
from app.db.database import Base, get_db
from app.db.models import PolicyDocument


@pytest_asyncio.fixture
async def client():
    """In-memory DB + two seeded policy chunks + an httpx client wired to the app.

    ``policy_117`` carries tags + source; ``policy_150`` has null tags to prove
    the endpoint coerces a missing list to ``[]`` rather than returning null.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as session:
        session.add_all(
            [
                PolicyDocument(
                    policy_key="policy_117",
                    title="Camera-Ready Deadline",
                    content="The camera-ready deadline is specified in AoE time.",
                    category="submission_deadlines",
                    score=1.0,
                    tags=["deadline", "camera-ready", "aoe"],
                    source="AAAI-27 call_for_papers.md",
                ),
                PolicyDocument(
                    policy_key="policy_150",
                    title="Reviewer Assignment",
                    content="Reviewers are assigned by the program committee.",
                    category="review_process",
                    score=None,
                    tags=None,
                    source="AAAI-27 reviewer_guidelines.md",
                ),
            ]
        )
        await session.commit()

    async def _override_get_db():
        async with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    main.app.dependency_overrides.clear()
    await engine.dispose()


async def test_get_policy_returns_full_chunk(client):
    resp = await client.get("/api/v1/policies/policy_117")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "policy_key",
        "title",
        "content",
        "category",
        "tags",
        "source",
        "score",
    }
    assert body["policy_key"] == "policy_117"
    assert body["title"] == "Camera-Ready Deadline"
    assert body["content"].startswith("The camera-ready deadline")
    assert body["category"] == "submission_deadlines"
    assert body["tags"] == ["deadline", "camera-ready", "aoe"]
    assert body["source"] == "AAAI-27 call_for_papers.md"


async def test_get_policy_coerces_null_tags_to_list(client):
    resp = await client.get("/api/v1/policies/policy_150")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tags"] == []
    assert body["score"] is None
    assert body["source"] == "AAAI-27 reviewer_guidelines.md"


async def test_get_policy_unknown_key_404(client):
    resp = await client.get("/api/v1/policies/policy_999")
    assert resp.status_code == 404
    assert "policy_999" in resp.json()["detail"]
