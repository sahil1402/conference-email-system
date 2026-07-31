"""PATCH /policies/suggestions/{id}/accept writes an append-only audit row.

Companion to test_suggestion_audit_reject.py (Piece 2b) — same harness, same
invariants. Closes the asymmetry where reject was audited and accept was not.

Accept's distinguishing detail: ``resulting_policy_key`` IS known at accept time
(``AcceptSuggestionRequest.policy_key`` is required — the frontend creates the
policy via POST /policies first), so the audit row carries the real key. That is
the field that links an accepted suggestion to what it actually became, so the
tests pin it rather than accepting a null.
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


async def _seed_suggestion(factory, *, title="Late registration grace period") -> int:
    """Insert one pending suggestion and return its id."""
    row = PolicySuggestion(
        source_email_id=7,
        experience_summary="Chair filled in the late-registration grace period.",
        title=title,
        content="Requests within 7 days of the deadline are granted automatically.",
    )
    async with factory() as s:
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return row.id


async def _audit_rows(factory, suggestion_id: int | None = None) -> list[SuggestionAuditLog]:
    """Audit rows for one suggestion, or the whole table when id is None."""
    async with factory() as s:
        stmt = select(SuggestionAuditLog).order_by(SuggestionAuditLog.id)
        if suggestion_id is not None:
            stmt = stmt.where(SuggestionAuditLog.suggestion_id == suggestion_id)
        return list((await s.execute(stmt)).scalars().all())


async def test_accept_writes_exactly_one_audit_row(client):
    c, factory = client
    sid = await _seed_suggestion(factory)

    resp = await c.patch(
        f"/api/v1/policies/suggestions/{sid}/accept",
        json={"actor": "Chair2", "policy_key": "int_late-registration__v1"},
    )
    assert resp.status_code == 200

    rows = await _audit_rows(factory, sid)
    assert len(rows) == 1
    entry = rows[0]
    assert entry.suggestion_id == sid
    assert entry.action == "accepted"
    assert entry.actor == "Chair2"
    # The load-bearing field for accepts: the policy this suggestion became.
    assert entry.resulting_policy_key == "int_late-registration__v1"
    # `reason` is reject-only; `details` is unused by both actions so far.
    assert entry.reason is None
    assert entry.details is None
    assert entry.timestamp is not None


async def test_logged_policy_key_comes_from_the_persisted_row(client):
    # The endpoint reads `row.resulting_policy_key`, not `payload.policy_key`, so
    # the audit records what was actually WRITTEN. This pins that the logged key
    # agrees with the suggestion row and with the response body — one value, three
    # places, no drift.
    c, factory = client
    sid = await _seed_suggestion(factory)

    resp = await c.patch(
        f"/api/v1/policies/suggestions/{sid}/accept",
        json={"actor": "Chair2", "policy_key": "int_grace-period"},
    )

    async with factory() as s:
        sugg = (
            await s.execute(select(PolicySuggestion).where(PolicySuggestion.id == sid))
        ).scalar_one()
    entry = (await _audit_rows(factory, sid))[0]

    assert entry.resulting_policy_key == sugg.resulting_policy_key == "int_grace-period"
    assert resp.json()["resulting_policy_key"] == "int_grace-period"


async def test_accept_defaults_actor_to_the_placeholder(client):
    # actor is optional (defaults to "Chair1"); the row records it as sent,
    # implying no verified identity — there is no auth.
    c, factory = client
    sid = await _seed_suggestion(factory)

    resp = await c.patch(
        f"/api/v1/policies/suggestions/{sid}/accept",
        json={"policy_key": "int_x"},
    )
    assert resp.status_code == 200

    rows = await _audit_rows(factory, sid)
    assert len(rows) == 1
    assert rows[0].actor == "Chair1"


async def test_missing_suggestion_404s_and_logs_nothing(client):
    # Guard-placement test (the 2b lesson): the log must sit AFTER the not-found
    # check. This is the ONLY test that fails if the call is moved before it —
    # the row-count tests above cannot distinguish "guarded" from "never writes".
    c, factory = client

    resp = await c.patch(
        "/api/v1/policies/suggestions/9999/accept",
        json={"actor": "Chair2", "policy_key": "int_x"},
    )
    assert resp.status_code == 404

    assert await _audit_rows(factory, 9999) == []
    assert await _audit_rows(factory) == []


async def test_existing_accept_behaviour_unchanged(client):
    # Regression guard: the audit write is ADDITIVE. Response body and every
    # PolicySuggestion field must behave exactly as before.
    c, factory = client
    sid = await _seed_suggestion(factory)

    resp = await c.patch(
        f"/api/v1/policies/suggestions/{sid}/accept",
        json={"actor": "Chair2", "policy_key": "int_late-registration"},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "id": sid,
        "status": "accepted",
        "resulting_policy_key": "int_late-registration",
    }

    async with factory() as s:
        row = (
            await s.execute(select(PolicySuggestion).where(PolicySuggestion.id == sid))
        ).scalar_one()
    assert row.status == "accepted"
    assert row.reviewed_by == "Chair2"
    assert row.resulting_policy_key == "int_late-registration"
    # Accept must not write the reject-side field.
    assert row.reviewed_reason is None


async def test_accept_and_reject_coexist_with_distinct_actions(client):
    """Both actions share one table; neither may clobber or mislabel the other."""
    c, factory = client
    accepted_id = await _seed_suggestion(factory, title="Accept me")
    rejected_id = await _seed_suggestion(factory, title="Reject me")

    await c.patch(
        f"/api/v1/policies/suggestions/{accepted_id}/accept",
        json={"actor": "Chair2", "policy_key": "int_accepted-one"},
    )
    await c.patch(
        f"/api/v1/policies/suggestions/{rejected_id}/reject",
        json={"actor": "Chair3", "reason": "Too specific."},
    )

    all_rows = await _audit_rows(factory)
    assert len(all_rows) == 2
    by_suggestion = {r.suggestion_id: r for r in all_rows}

    accept_row = by_suggestion[accepted_id]
    assert accept_row.action == "accepted"
    assert accept_row.actor == "Chair2"
    assert accept_row.resulting_policy_key == "int_accepted-one"
    assert accept_row.reason is None

    reject_row = by_suggestion[rejected_id]
    assert reject_row.action == "rejected"
    assert reject_row.actor == "Chair3"
    assert reject_row.reason == "Too specific."
    # The reject path never sets a policy key — that is accept-only.
    assert reject_row.resulting_policy_key is None


async def test_accept_after_reject_appends_rather_than_overwriting(client):
    # Append-only across DIFFERENT action types, not just repeats of one: a chair
    # who rejects then changes their mind must leave both facts on the record.
    # `mark_accepted` overwrites reviewed_by in place, so current state keeps only
    # the accept — the audit table is the only place the reject survives.
    c, factory = client
    sid = await _seed_suggestion(factory)

    await c.patch(
        f"/api/v1/policies/suggestions/{sid}/reject",
        json={"actor": "Chair2", "reason": "Not generalizable."},
    )
    await c.patch(
        f"/api/v1/policies/suggestions/{sid}/accept",
        json={"actor": "Chair3", "policy_key": "int_reconsidered"},
    )

    rows = await _audit_rows(factory, sid)
    assert [r.action for r in rows] == ["rejected", "accepted"]
    assert [r.actor for r in rows] == ["Chair2", "Chair3"]

    async with factory() as s:
        sugg = (
            await s.execute(select(PolicySuggestion).where(PolicySuggestion.id == sid))
        ).scalar_one()
    assert sugg.status == "accepted"
    assert sugg.reviewed_by == "Chair3"
    # The rejection reason survives ONLY in the audit trail: mark_accepted does
    # not clear reviewed_reason, so current state is now self-contradictory
    # (accepted, yet carrying a rejection reason) — which is precisely why the
    # append-only log is the authoritative history.
    assert rows[0].reason == "Not generalizable."
