"""Tests for chair-edit diff persistence (Phase 5F, Part 1).

Verifies that approving with an edited draft preserves the original text
alongside the edit and records both full texts in the audit log, while
approving with unchanged text records no diff. Uses an in-memory SQLite DB via
ASGITransport; no network, no API key.
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
from app.db.models import Email

_ORIGINAL = "Thank you for your question. The deadline is in AoE time. [policy_002]"
_EDITED = "Hi there — the submission deadline is in Anywhere on Earth (AoE) time. Best, the chairs."


class _Ctx:
    def __init__(self, client, factory):
        self.client = client
        self.factory = factory


async def _seed_email(factory, draft_text: str) -> int:
    """Insert a human_review email carrying a drafted reply; return its id."""
    async with factory() as session:
        email = Email(
            sender="author@university.edu",
            subject="Deadline?",
            body="When is the deadline?",
            status="DRAFT_GENERATED",
            routing={"lane": "human_review", "reason": "sensitive"},
            classification={"intent": "submission_deadline", "confidence": 0.6},
            draft={"draft_text": draft_text, "citations": ["policy_002"], "model_used": "none"},
        )
        session.add(email)
        await session.commit()
        await session.refresh(email)
        return email.id


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

    async def _override_get_db():
        async with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield _Ctx(client, factory)

    main.app.dependency_overrides.clear()
    await engine.dispose()


async def _audit_entries(client, email_id: int) -> list[dict]:
    resp = await client.get("/api/v1/audit", params={"email_id": str(email_id)})
    assert resp.status_code == 200
    return resp.json()["items"]


# ---------------------------------------------------------------------------
# Edited approve → original preserved + diff captured
# ---------------------------------------------------------------------------
async def test_edit_preserves_original_and_sets_edited_text(ctx):
    email_id = await _seed_email(ctx.factory, _ORIGINAL)

    resp = await ctx.client.patch(
        f"/api/v1/emails/{email_id}/approve",
        json={"approved_by": "chair", "final_text": _EDITED},
    )
    assert resp.status_code == 200
    draft = resp.json()["draft"]

    # Original preserved; edited text is now the current draft.
    assert draft["original_draft_text"] == _ORIGINAL
    assert draft["draft_text"] == _EDITED
    assert draft["is_edited"] is True


async def test_edit_records_both_texts_in_audit(ctx):
    email_id = await _seed_email(ctx.factory, _ORIGINAL)
    await ctx.client.patch(
        f"/api/v1/emails/{email_id}/approve",
        json={"approved_by": "chair", "final_text": _EDITED},
    )

    approved = [e for e in await _audit_entries(ctx.client, email_id) if e["action"] == "approved"]
    assert len(approved) == 1
    details = approved[0]["details"]
    assert details["edited"] is True
    # Both full texts are retained so the diff can be reconstructed (5G).
    assert details["original_draft"] == _ORIGINAL
    assert details["edited_draft"] == _EDITED


# ---------------------------------------------------------------------------
# Unchanged approve → no spurious diff
# ---------------------------------------------------------------------------
async def test_approve_without_edit_records_no_diff(ctx):
    email_id = await _seed_email(ctx.factory, _ORIGINAL)
    # Same text (identical ≠ an edit).
    resp = await ctx.client.patch(
        f"/api/v1/emails/{email_id}/approve",
        json={"approved_by": "chair", "final_text": _ORIGINAL},
    )
    assert resp.status_code == 200
    draft = resp.json()["draft"]
    assert "is_edited" not in draft
    assert "original_draft_text" not in draft

    approved = [e for e in await _audit_entries(ctx.client, email_id) if e["action"] == "approved"]
    assert approved[0]["details"]["edited"] is False
    assert "original_draft" not in approved[0]["details"]
    assert "edited_draft" not in approved[0]["details"]


async def test_approve_with_no_final_text_is_not_an_edit(ctx):
    email_id = await _seed_email(ctx.factory, _ORIGINAL)
    resp = await ctx.client.patch(
        f"/api/v1/emails/{email_id}/approve",
        json={"approved_by": "chair"},
    )
    assert resp.status_code == 200
    approved = [e for e in await _audit_entries(ctx.client, email_id) if e["action"] == "approved"]
    assert approved[0]["details"]["edited"] is False


async def test_whitespace_only_change_is_not_an_edit(ctx):
    email_id = await _seed_email(ctx.factory, _ORIGINAL)
    resp = await ctx.client.patch(
        f"/api/v1/emails/{email_id}/approve",
        json={"approved_by": "chair", "final_text": f"  {_ORIGINAL}  "},
    )
    assert resp.status_code == 200
    approved = [e for e in await _audit_entries(ctx.client, email_id) if e["action"] == "approved"]
    assert approved[0]["details"]["edited"] is False


# ---------------------------------------------------------------------------
# Send-gate: unresolved [CHAIR: ...] placeholders block approval (Phase 7F)
# ---------------------------------------------------------------------------
_PLACEHOLDER_DRAFT = (
    "Dear author, you can update the affiliation by "
    "[CHAIR: fill in the CRC update procedure]."
)


async def test_approve_blocked_while_placeholders_remain(ctx):
    email_id = await _seed_email(ctx.factory, _PLACEHOLDER_DRAFT)
    resp = await ctx.client.patch(
        f"/api/v1/emails/{email_id}/approve",
        json={"approved_by": "chair"},
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["placeholders"] == ["fill in the CRC update procedure"]
    # Nothing was approved or audited — the email still awaits the chair's edit.
    assert await _audit_entries(ctx.client, email_id) == []


async def test_approve_blocked_when_edit_still_has_placeholder(ctx):
    email_id = await _seed_email(ctx.factory, _PLACEHOLDER_DRAFT)
    resp = await ctx.client.patch(
        f"/api/v1/emails/{email_id}/approve",
        json={
            "approved_by": "chair",
            "final_text": "Dear author, [CHAIR: decide the procedure] applies.",
        },
    )
    assert resp.status_code == 409


async def test_approve_succeeds_once_placeholders_resolved(ctx):
    email_id = await _seed_email(ctx.factory, _PLACEHOLDER_DRAFT)
    fixed = (
        "Dear author, you can update the affiliation by emailing the "
        "publications chairs with your paper id."
    )
    resp = await ctx.client.patch(
        f"/api/v1/emails/{email_id}/approve",
        json={"approved_by": "chair", "final_text": fixed},
    )
    assert resp.status_code == 200
    draft = resp.json()["draft"]
    assert draft["draft_text"] == fixed
    assert draft["original_draft_text"] == _PLACEHOLDER_DRAFT
