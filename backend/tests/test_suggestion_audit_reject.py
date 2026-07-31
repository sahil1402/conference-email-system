"""PATCH /policies/suggestions/{id}/reject writes an append-only audit row.

Same throwaway-in-memory-SQLite harness as test_policy_versioning.py.

The point of ``suggestion_audit_logs`` is that it is HISTORY, not state:
``PolicySuggestion.reviewed_by`` / ``reviewed_reason`` already hold the latest
reviewer and are overwritten on every action. So the load-bearing assertions
here are the ones about row COUNT and ordering, not just field contents.
"""
import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from app.db.database import get_db
from app.db.models import Base, PolicySuggestion, SuggestionAuditLog


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


async def _seed_suggestion(factory, **overrides) -> int:
    """Insert one pending suggestion and return its id."""
    row = PolicySuggestion(
        source_email_id=overrides.pop("source_email_id", 7),
        experience_summary="Chair filled in the late-registration grace period.",
        title="Late registration grace period",
        content="Requests within 7 days of the deadline are granted automatically.",
        **overrides,
    )
    async with factory() as s:
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return row.id


async def _audit_rows(factory, suggestion_id: int) -> list[SuggestionAuditLog]:
    async with factory() as s:
        stmt = (
            select(SuggestionAuditLog)
            .where(SuggestionAuditLog.suggestion_id == suggestion_id)
            .order_by(SuggestionAuditLog.id)
        )
        return list((await s.execute(stmt)).scalars().all())


async def test_reject_writes_exactly_one_audit_row(client):
    c, factory = client
    sid = await _seed_suggestion(factory)

    resp = await c.patch(
        f"/api/v1/policies/suggestions/{sid}/reject",
        json={"actor": "Chair2", "reason": "Too ticket-specific to generalize."},
    )
    assert resp.status_code == 200

    rows = await _audit_rows(factory, sid)
    assert len(rows) == 1
    entry = rows[0]
    assert entry.suggestion_id == sid
    assert entry.action == "rejected"
    assert entry.actor == "Chair2"
    assert entry.reason == "Too ticket-specific to generalize."
    # Reject-only fields: these belong to accepts (Piece 2c).
    assert entry.resulting_policy_key is None
    assert entry.details is None
    # server_default=func.now() must actually populate.
    assert entry.timestamp is not None


async def test_reject_without_a_reason_still_logs(client):
    # `reason` is optional in RejectSuggestionRequest, so the audit row must be
    # written for a bare reject too — a null reason is not a reason to skip it.
    c, factory = client
    sid = await _seed_suggestion(factory)

    resp = await c.patch(f"/api/v1/policies/suggestions/{sid}/reject", json={})
    assert resp.status_code == 200

    rows = await _audit_rows(factory, sid)
    assert len(rows) == 1
    assert rows[0].action == "rejected"
    assert rows[0].reason is None
    # RejectSuggestionRequest defaults actor to the "Chair1" placeholder; the log
    # records whatever the client sent, implying no verified identity.
    assert rows[0].actor == "Chair1"


async def test_missing_suggestion_404s_and_logs_nothing(client):
    c, factory = client

    resp = await c.patch(
        "/api/v1/policies/suggestions/9999/reject", json={"actor": "Chair2"}
    )
    assert resp.status_code == 404

    # Scoped to the bad id AND globally: a 404 must not write anywhere.
    assert await _audit_rows(factory, 9999) == []
    async with factory() as s:
        total = (await s.execute(select(SuggestionAuditLog))).scalars().all()
    assert total == []


async def test_re_rejecting_appends_a_second_row_rather_than_overwriting(client):
    """The whole reason this table exists.

    ``SuggestionRepository.reject`` overwrites ``reviewed_by`` / ``reviewed_reason``
    in place, so the first reviewer's reason is destroyed on a second reject. The
    audit table must keep both, in order.
    """
    c, factory = client
    sid = await _seed_suggestion(factory)

    first = await c.patch(
        f"/api/v1/policies/suggestions/{sid}/reject",
        json={"actor": "Chair2", "reason": "First call: not generalizable."},
    )
    second = await c.patch(
        f"/api/v1/policies/suggestions/{sid}/reject",
        json={"actor": "Chair3", "reason": "Second call: still no."},
    )
    assert first.status_code == 200
    assert second.status_code == 200

    rows = await _audit_rows(factory, sid)
    assert len(rows) == 2
    assert [r.actor for r in rows] == ["Chair2", "Chair3"]
    assert [r.reason for r in rows] == [
        "First call: not generalizable.",
        "Second call: still no.",
    ]
    assert {r.action for r in rows} == {"rejected"}

    # And the contrast that motivates the table: the suggestion row itself kept
    # only the LAST reviewer — the first is gone from current state.
    async with factory() as s:
        sugg = (
            await s.execute(select(PolicySuggestion).where(PolicySuggestion.id == sid))
        ).scalar_one()
    assert sugg.reviewed_by == "Chair3"
    assert sugg.reviewed_reason == "Second call: still no."


async def test_existing_reject_behaviour_unchanged(client):
    # Regression guard: the audit write is ADDITIVE. Status, reviewed_by,
    # reviewed_reason and the response body must all behave exactly as before.
    c, factory = client
    sid = await _seed_suggestion(factory)

    resp = await c.patch(
        f"/api/v1/policies/suggestions/{sid}/reject",
        json={"actor": "Chair2", "reason": "One-off."},
    )

    assert resp.status_code == 200
    assert resp.json() == {"id": sid, "status": "rejected"}

    async with factory() as s:
        row = (
            await s.execute(select(PolicySuggestion).where(PolicySuggestion.id == sid))
        ).scalar_one()
    assert row.status == "rejected"
    assert row.reviewed_by == "Chair2"
    assert row.reviewed_reason == "One-off."
    # Reject must not touch the accept-side link.
    assert row.resulting_policy_key is None


async def test_rejected_row_still_visible_to_dedup(client):
    # `find_similar` queries status in ("pending", "rejected") — rejected rows are
    # load-bearing for CEL dedup, so the reject path must not remove them.
    c, factory = client
    sid = await _seed_suggestion(factory)

    await c.patch(f"/api/v1/policies/suggestions/{sid}/reject", json={})

    async with factory() as s:
        rows = (await s.execute(select(PolicySuggestion))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "rejected"
