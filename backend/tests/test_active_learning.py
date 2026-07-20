"""Tests for active-learning candidate flagging (Phase 5G).

Covers the two flagging predicates on clear positive/negative cases, their
co-occurrence, and the candidates endpoint shape. The predicates read
thresholds from settings (FAQ_CONFIDENCE_THRESHOLD=0.65, AL_CONFIDENCE_MARGIN=
0.15, AL_EDIT_RATIO=0.15), so the confidence band flagged is [0.50, 0.65).
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
from app.pipeline.active_learning import (
    FLAG_LOW_CONFIDENCE,
    FLAG_MEANINGFUL_EDIT,
    build_flag_events,
    edit_change_ratio,
    should_flag_low_confidence,
    should_flag_meaningful_edit,
)

_ORIGINAL = (
    "Thank you for your question about the submission deadline. The full paper "
    "deadline is specified in Anywhere on Earth time and late submissions are not accepted."
)


# ---------------------------------------------------------------------------
# Low-confidence predicate
# ---------------------------------------------------------------------------
def test_low_confidence_flags_near_miss():
    # 0.60 sits in [0.50, 0.65) → a near-miss the human rescued → flag.
    assert should_flag_low_confidence({"confidence": 0.60}) is True


def test_low_confidence_not_flagged_comfortably_above():
    # 0.85 comfortably passed the threshold → not a near-miss.
    assert should_flag_low_confidence({"confidence": 0.85}) is False


def test_low_confidence_not_flagged_far_below():
    # 0.30 is far below the band — clearly human-review territory, not "lucky".
    assert should_flag_low_confidence({"confidence": 0.30}) is False


def test_low_confidence_prefers_calibrated_value():
    # Raw is a near-miss but the CALIBRATED value (what the router used) is high
    # → not flagged, mirroring the Phase 5B router seam.
    c = {"confidence": 0.60, "calibrated_confidence": 0.82}
    assert should_flag_low_confidence(c) is False
    # And the inverse: calibrated lands in the band → flagged.
    assert should_flag_low_confidence({"confidence": 0.95, "calibrated_confidence": 0.60}) is True


# ---------------------------------------------------------------------------
# Meaningful-edit predicate
# ---------------------------------------------------------------------------
def test_typo_fix_not_flagged():
    typo = _ORIGINAL.replace("Anywhere", "Anywere")  # one word changed
    assert should_flag_meaningful_edit(_ORIGINAL, typo) is False


def test_substantial_rewrite_flagged():
    rewrite = (
        "Hi there! Deadlines this year moved — please check the portal directly, and "
        "reach out to the chairs if you need an extension for exceptional circumstances."
    )
    assert should_flag_meaningful_edit(_ORIGINAL, rewrite) is True


def test_identical_text_not_flagged():
    assert should_flag_meaningful_edit(_ORIGINAL, _ORIGINAL) is False
    assert edit_change_ratio(_ORIGINAL, _ORIGINAL) == 0.0


# ---------------------------------------------------------------------------
# build_flag_events — co-occurrence + separation
# ---------------------------------------------------------------------------
def test_both_flags_cooccur_as_separate_events():
    rewrite = "Completely different reply text that shares almost nothing with the original draft."
    events = build_flag_events(
        {"confidence": 0.58},
        was_edited=True,
        original_text=_ORIGINAL,
        edited_text=rewrite,
    )
    actions = {a for a, _ in events}
    # Two DISTINCT events, never merged into one generic flag.
    assert actions == {FLAG_LOW_CONFIDENCE, FLAG_MEANINGFUL_EDIT}
    assert len(events) == 2


def test_no_flags_on_clean_baseline_email():
    # High confidence, no edit → the straightforward baseline stays unflagged.
    events = build_flag_events({"confidence": 0.92}, was_edited=False)
    assert events == []


def test_edit_signal_ignored_when_not_edited():
    # was_edited=False → meaningful-edit is never considered even if texts differ.
    events = build_flag_events(
        {"confidence": 0.92},
        was_edited=False,
        original_text=_ORIGINAL,
        edited_text="totally different",
    )
    assert events == []


# ---------------------------------------------------------------------------
# Candidates endpoint
# ---------------------------------------------------------------------------
class _Ctx:
    def __init__(self, client, factory):
        self.client = client
        self.factory = factory


async def _seed_email(factory, confidence: float, draft_text: str) -> int:
    async with factory() as session:
        email = Email(
            sender="author@university.edu",
            subject="Deadline question",
            body="When is the deadline?",
            status="DRAFT_GENERATED",
            routing={"lane": "human_review", "reason": "below threshold", "confidence_used": confidence},
            classification={"intent": "submission_requirements", "confidence": confidence},
            draft={"draft_text": draft_text, "citations": [], "model_used": "none"},
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


async def test_candidates_endpoint_shape_and_flagging(ctx):
    # Email A: near-miss confidence + a big rewrite on approve → both flags.
    a = await _seed_email(ctx.factory, 0.58, _ORIGINAL)
    rewrite = "A totally rewritten reply that keeps essentially none of the original wording here."
    await ctx.client.patch(
        f"/api/v1/emails/{a}/approve",
        json={"approved_by": "chair", "final_text": rewrite},
    )

    # Email B: high confidence, approved as-is → NOT a candidate.
    b = await _seed_email(ctx.factory, 0.95, _ORIGINAL)
    await ctx.client.patch(
        f"/api/v1/emails/{b}/approve",
        json={"approved_by": "chair", "final_text": _ORIGINAL},
    )

    resp = await ctx.client.get("/api/v1/analytics/active-learning-candidates")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"candidates", "total"}

    by_id = {c["email_id"]: c for c in body["candidates"]}
    # A is flagged for BOTH reasons; B is absent.
    assert str(a) in by_id
    assert str(b) not in by_id
    cand = by_id[str(a)]
    assert cand["reason"] == "both"
    assert cand["subject"] == "Deadline question"
    assert cand["low_confidence"]["confidence_used"] == 0.58
    assert cand["meaningful_edit"]["change_ratio"] > 0.15


async def test_candidates_empty_when_nothing_flagged(ctx):
    b = await _seed_email(ctx.factory, 0.95, _ORIGINAL)
    await ctx.client.patch(
        f"/api/v1/emails/{b}/approve",
        json={"approved_by": "chair", "final_text": _ORIGINAL},
    )
    body = (await ctx.client.get("/api/v1/analytics/active-learning-candidates")).json()
    assert body["total"] == 0
    assert body["candidates"] == []
