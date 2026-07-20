"""Regression tests for analytics aggregates that must span ALL emails/events,
not a capped client page.

Same class of bug as the chair-surface fixes: a chart derived its numbers from
the newest-20 queue page, so anything older was invisible. These assert the
server-side aggregates count the full set.

Seeds put the "interesting" rows OUTSIDE the newest-20 window (older timestamps,
lower ids) so a page-derived count would miss them.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

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
from app.db.models import AuditLog, Email

_BASE_TIME = datetime(2026, 1, 1, 12, 0, 0)
_N_HIGH = 22   # high-confidence, NEW  -> fill the newest page
_N_LOW = 3     # low-confidence, OLD   -> outside the newest-20 window


def _email(idx: int, confidence: float, received_at: datetime) -> Email:
    return Email(
        sender=f"user{idx}@univ.edu",
        subject=f"email {idx}",
        body="body",
        status="DRAFT_GENERATED",
        classification={"intent": "submission_requirements", "confidence": confidence},
        routing={"lane": "faq" if confidence >= 0.65 else "human_review"},
        received_at=received_at,
    )


@pytest_asyncio.fixture
async def ctx():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as session:
        # Low-confidence OLD emails first (older timestamps => sorted last).
        for i in range(_N_LOW):
            session.add(_email(i, 0.30, _BASE_TIME + timedelta(minutes=i)))
        # High-confidence NEW emails fill the first page and beyond.
        for i in range(_N_HIGH):
            session.add(_email(100 + i, 0.95, _BASE_TIME + timedelta(hours=1, minutes=i)))
        await session.commit()

    async def _override_get_db():
        async with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(client=client, factory=factory)

    main.app.dependency_overrides.clear()
    await engine.dispose()


# --- Fix 1: Confidence Distribution ----------------------------------------
async def test_confidence_distribution_counts_all_emails(ctx):
    """The histogram counts the out-of-window low-confidence emails, not just the
    newest page."""
    dist = (await ctx.client.get("/api/v1/analytics/summary")).json()[
        "confidence_distribution"
    ]
    # Ordered low -> high; 6 bands.
    assert [d["band"] for d in dist] and len(dist) == 6
    assert dist[0]["count"] == _N_LOW    # lowest band "0-0.5" -> the 3 old ones
    assert dist[-1]["count"] == _N_HIGH  # highest band "0.9-1.0" -> the 22
    assert sum(d["count"] for d in dist) == _N_LOW + _N_HIGH


async def test_generic_page_would_miss_low_confidence_documenting_the_bug(ctx):
    """The old source (newest-20 queue page) contains zero low-confidence emails,
    so the page-derived histogram showed 0 in the lowest band."""
    page = (await ctx.client.get("/api/v1/emails/queue")).json()["emails"]
    assert len(page) == 20
    low_on_page = [
        e for e in page if (e["classification"] or {}).get("confidence", 1) < 0.5
    ]
    assert low_on_page == []


# --- Fix 3: Auto-Replies avg confidence over the FULL faq set ---------------
@pytest_asyncio.fixture
async def faq_ctx():
    """20 NEW faq emails at confidence 1.0 (fill the page) + 5 OLD faq emails at
    0.70 (outside the newest-20). Full-set avg (0.94) ≠ page avg (1.0)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    def _faq(idx: int, conf: float, received_at: datetime) -> Email:
        return Email(
            sender=f"a{idx}@u.edu", subject=f"faq {idx}", body="b",
            status="DRAFT_GENERATED",
            classification={"intent": "submission_requirements", "confidence": conf},
            routing={"lane": "faq"}, received_at=received_at,
        )

    async with factory() as session:
        for i in range(5):   # OLD, low-ish, outside the page
            session.add(_faq(i, 0.70, _BASE_TIME + timedelta(minutes=i)))
        for i in range(20):  # NEW, top confidence, fill the page
            session.add(_faq(100 + i, 1.0, _BASE_TIME + timedelta(hours=1, minutes=i)))
        await session.commit()

    async def _override_get_db():
        async with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(client=client)
    main.app.dependency_overrides.clear()
    await engine.dispose()


async def test_faq_avg_confidence_uses_full_set_not_page(faq_ctx):
    """faq_avg_confidence averages ALL 25 faq emails (0.94), not the newest-20
    page (which is all 1.0)."""
    summary = (await faq_ctx.client.get("/api/v1/analytics/summary")).json()
    assert summary["faq_avg_confidence"] == 0.94  # (20*1.0 + 5*0.70) / 25

    # The old page-derived source: the newest-20 faq page is entirely 1.0.
    page = (await faq_ctx.client.get(
        "/api/v1/emails/queue", params={"lane": "faq"}
    )).json()["emails"]
    assert len(page) == 20
    page_avg = sum(e["classification"]["confidence"] for e in page) / len(page)
    assert page_avg == 1.0  # what the client used to display — wrong


# --- Fix 4: Reassignments by Chair over ALL audit rows (past the 200 cap) ----
_N_CHAIR1 = 205   # > the /audit limit cap of 200
_N_CHAIR2 = 3
_N_UNASSIGNED = 2


@pytest_asyncio.fixture
async def reassign_ctx():
    """Seed chair_reassigned audit rows exceeding the audit feed's 200-row cap:
    205 moved away from chair 1, 3 from chair 2, 2 from no chair."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as session:
        email = Email(
            sender="a@u.edu", subject="s", body="b", status="DRAFT_GENERATED",
            classification={"intent": "reviewer_assignment", "confidence": 0.9},
            routing={"lane": "human_review"},
        )
        session.add(email)
        await session.commit()
        await session.refresh(email)
        eid = email.id

        def _entry(original: int | None) -> AuditLog:
            return AuditLog(
                email_id=eid, action="chair_reassigned", actor="chair",
                extra_metadata={"original_chair_id": original, "new_chair_id": 9},
            )

        for _ in range(_N_CHAIR1):
            session.add(_entry(1))
        for _ in range(_N_CHAIR2):
            session.add(_entry(2))
        for _ in range(_N_UNASSIGNED):
            session.add(_entry(None))
        await session.commit()

    async def _override_get_db():
        async with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(client=client)
    main.app.dependency_overrides.clear()
    await engine.dispose()


async def test_reassignment_by_chair_counts_past_the_audit_cap(reassign_ctx):
    """The aggregate counts ALL chair_reassigned rows — including chair 1's 205,
    which exceeds the 200-row audit page the old chart tallied."""
    dist = (await reassign_ctx.client.get("/api/v1/analytics/summary")).json()[
        "reassignment_by_chair"
    ]
    assert dist["1"] == _N_CHAIR1          # 205 > 200 cap
    assert dist["2"] == _N_CHAIR2
    assert dist["unassigned"] == _N_UNASSIGNED


async def test_audit_feed_is_capped_documenting_the_bug(reassign_ctx):
    """The old source (/audit?action=chair_reassigned) is capped at 200 items,
    so a client tally over it undercounted chair 1 (200 seen of 205)."""
    body = (await reassign_ctx.client.get(
        "/api/v1/audit", params={"action": "chair_reassigned", "limit": 200}
    )).json()
    assert body["total"] == _N_CHAIR1 + _N_CHAIR2 + _N_UNASSIGNED  # 210 exist
    assert len(body["items"]) == 200  # but only 200 come back → client undercounts
