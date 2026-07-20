"""Tests for the send gate — human approval as the transport precondition.

The gate itself is pure (no I/O), so most cases run on plain namespace
objects; the endpoint tests use the in-memory-DB + ASGITransport pattern to
prove the gate is enforced (and audited) at the API seam a future transport
will hang off.
"""

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
from app.core.config import settings
from app.core.send_gate import authorize_send
from app.db.database import Base, get_db
from app.db.models import Email

_CLEAN = "Dear author, the deadline is in AoE time."
_PLACEHOLDER = "Dear author, the deadline is [CHAIR: confirm the deadline]."


def _email(status="DRAFT_GENERATED", lane="human_review", text=_CLEAN, leaks=None):
    draft = {"draft_text": text}
    if leaks is not None:
        draft["generation_metadata"] = {"reply_leaks": leaks}
    return SimpleNamespace(status=status, routing={"lane": lane}, draft=draft)


# ---------------------------------------------------------------------------
# Default policy: approval is the only road out, regardless of lane
# ---------------------------------------------------------------------------
def test_approved_email_is_authorized():
    decision = authorize_send(_email(status="approved"))
    assert decision.authorized is True
    assert decision.mode == "approved"


def test_unapproved_faq_email_is_refused_by_default():
    decision = authorize_send(_email(lane="faq"))
    assert decision.authorized is False
    assert "approval required" in decision.reason.lower()


def test_unapproved_human_review_email_is_refused():
    assert authorize_send(_email()).authorized is False


def test_placeholders_block_even_an_approved_email():
    decision = authorize_send(_email(status="approved", text=_PLACEHOLDER))
    assert decision.authorized is False
    assert "placeholder" in decision.reason.lower()


def test_empty_draft_is_refused():
    assert authorize_send(_email(status="approved", text="  ")).authorized is False


# ---------------------------------------------------------------------------
# ALLOW_AUTO_SEND=True: complete FAQ drafts may go; everything else still gated
# ---------------------------------------------------------------------------
def test_auto_send_releases_complete_faq_draft(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_AUTO_SEND", True)
    decision = authorize_send(_email(lane="faq"))
    assert decision.authorized is True
    assert decision.mode == "auto"


def test_auto_send_never_releases_human_review_lane(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_AUTO_SEND", True)
    assert authorize_send(_email(lane="human_review")).authorized is False


def test_auto_send_never_releases_placeholder_faq_draft(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_AUTO_SEND", True)
    assert authorize_send(_email(lane="faq", text=_PLACEHOLDER)).authorized is False


def test_auto_send_never_releases_leak_flagged_faq_draft(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_AUTO_SEND", True)
    decision = authorize_send(_email(lane="faq", leaks=["we will look into"]))
    assert decision.authorized is False
    assert "leak" in decision.reason.lower()


# ---------------------------------------------------------------------------
# Endpoint: the gate is enforced and audited at the transport seam
# ---------------------------------------------------------------------------
class _Ctx:
    def __init__(self, client, factory):
        self.client = client
        self.factory = factory


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


async def _seed(factory, status="DRAFT_GENERATED") -> int:
    async with factory() as session:
        email = Email(
            sender="author@u.edu",
            subject="Deadline?",
            body="When is the deadline?",
            status=status,
            routing={"lane": "human_review", "reason": "test"},
            classification={"intent": "submission_requirements", "confidence": 0.6},
            draft={"draft_text": _CLEAN, "citations": [], "model_used": "none"},
        )
        session.add(email)
        await session.commit()
        await session.refresh(email)
        return email.id


async def _audit_actions(client, email_id):
    resp = await client.get("/api/v1/audit", params={"email_id": str(email_id)})
    return [(e["action"], e["details"]) for e in resp.json()["items"]]


async def test_send_endpoint_blocks_unapproved_and_audits(ctx):
    email_id = await _seed(ctx.factory)
    resp = await ctx.client.post(f"/api/v1/emails/{email_id}/send")
    assert resp.status_code == 409
    assert "approval required" in resp.json()["detail"]["reason"].lower()
    actions = await _audit_actions(ctx.client, email_id)
    assert actions and actions[-1][0] == "send_blocked"


async def test_send_endpoint_authorizes_approved_but_lacks_transport(ctx):
    email_id = await _seed(ctx.factory)
    approve = await ctx.client.patch(
        f"/api/v1/emails/{email_id}/approve", json={"approved_by": "chair"}
    )
    assert approve.status_code == 200

    resp = await ctx.client.post(f"/api/v1/emails/{email_id}/send")
    # Gate passed; transport does not exist yet → 501, draft stays queued.
    assert resp.status_code == 501
    assert resp.json()["detail"]["mode"] == "approved"
    actions = await _audit_actions(ctx.client, email_id)
    assert ("send_authorized", {"mode": "approved",
            "reason": "Chair-approved draft."}) in actions
