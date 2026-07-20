"""Strategy-independent safety floor: a draft with chair placeholders or
notes-for-chair must NEVER reach the FAQ lane, whatever the router decided.

This is defense-in-depth. The default ``rule_based`` router already refuses to
route such a draft to "faq" via its own draft-quality gate (see
``app.pipeline.router.EmailRouter.route``) — so under the default strategy
this floor is redundant. It is load-bearing for the ``rl`` strategy
(``ROUTING_STRATEGY=rl``), whose bandit decides the lane from intent+confidence
BEFORE a draft exists and never sees the draft at all. These tests prove the
floor fires independent of what the router returns, by injecting a stub router
that always returns "faq" — exactly the RL-path scenario in miniature, without
depending on the bandit's own (unrelated) exploration logic.
"""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.pipeline.drafter import DraftResponse
from app.pipeline.orchestrator import EmailPipeline
from app.pipeline.router import LANE_FAQ, LANE_HUMAN_REVIEW, RoutingDecision
from app.repositories.email_repository import EmailRepository


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


class _AlwaysFaqRouter:
    """Stub router that routes to "faq" no matter what — draft-blind, exactly
    like the RL strategy, which decides the lane from intent+confidence before
    a draft exists and never inspects it."""

    def route(self, classification, retrieved_chunks, draft):
        return RoutingDecision(
            lane=LANE_FAQ,
            reason="stub: always faq (simulates a draft-blind routing strategy)",
            confidence_used=classification.confidence,
            threshold_applied=0.0,
        )


class _PlaceholderDrafter:
    """Stub drafter that always returns a draft with an unresolved chair
    placeholder — i.e. not self-sufficient."""

    provider = "stub"

    async def draft(self, email_data, classification, retrieved_chunks):
        return DraftResponse(
            draft_text="Thanks for reaching out. [CHAIR: confirm exact date].",
            notes_for_chair=None,
            placeholders=["[CHAIR: confirm exact date]"],
            citations=["policy_101"],
            answer_confidence=0.95,
            model_used="stub",
        )


class _NotesDrafter:
    """Stub drafter whose reply has no placeholders but carries chair notes."""

    provider = "stub"

    async def draft(self, email_data, classification, retrieved_chunks):
        return DraftResponse(
            draft_text="Thanks for reaching out — here is the policy.",
            notes_for_chair="Requester's case is borderline; please double-check.",
            placeholders=[],
            citations=["policy_101"],
            answer_confidence=0.95,
            model_used="stub",
        )


async def test_floor_overrides_faq_lane_when_draft_has_placeholders(session):
    """Independent of the router's decision: a placeholder draft can never
    stay in the FAQ lane, even when the (stubbed) router says "faq"."""
    pipeline = EmailPipeline()
    pipeline.router = _AlwaysFaqRouter()
    pipeline.drafter = _PlaceholderDrafter()

    result = await pipeline.process_email(
        {"from": "a@b.com", "subject": "Deadline?", "body": "When is the deadline?"},
        session,
    )

    assert result.routing.lane == LANE_HUMAN_REVIEW
    assert result.routing.override_reason is not None
    assert "placeholder" in result.routing.override_reason

    # Persisted record agrees with the in-memory result.
    email = await EmailRepository().get_email_by_id(session, result.email_id)
    assert email.routing["lane"] == LANE_HUMAN_REVIEW


async def test_floor_overrides_faq_lane_when_draft_has_notes_for_chair(session):
    """Same floor, triggered by notes_for_chair instead of placeholders."""
    pipeline = EmailPipeline()
    pipeline.router = _AlwaysFaqRouter()
    pipeline.drafter = _NotesDrafter()

    result = await pipeline.process_email(
        {"from": "a@b.com", "subject": "Appeal", "body": "I would like to appeal."},
        session,
    )

    assert result.routing.lane == LANE_HUMAN_REVIEW
    assert result.routing.override_reason is not None
    assert "notes=yes" in result.routing.override_reason


async def test_floor_does_not_fire_on_a_self_sufficient_draft(session):
    """Control: a draft with no placeholders/notes is left alone even when the
    stub router says "faq" — the floor must not clobber a legitimate decision."""

    class _CleanDrafter:
        provider = "stub"

        async def draft(self, email_data, classification, retrieved_chunks):
            return DraftResponse(
                draft_text="Thanks — here is the policy answer.",
                notes_for_chair=None,
                placeholders=[],
                citations=["policy_101"],
                answer_confidence=0.95,
                model_used="stub",
            )

    pipeline = EmailPipeline()
    pipeline.router = _AlwaysFaqRouter()
    pipeline.drafter = _CleanDrafter()

    result = await pipeline.process_email(
        {"from": "a@b.com", "subject": "Deadline?", "body": "When is the deadline?"},
        session,
    )

    assert result.routing.lane == LANE_FAQ
    assert result.routing.override_reason is None
