"""Integration tests for multi-chair routing (Phase 6A, Step 3).

Exercises the full path end-to-end against a throwaway in-memory SQLite DB
(StaticPool keeps every connection on the same ``:memory:`` database), with the
five standing chairs seeded to match the Phase 6A migration:

  classify → retrieve → route → (chair assign) → persist  ... and reassignment.

Two harnesses share one DB:
- the real ``EmailPipeline`` driven directly (dataset-driven assignment), and
- the app driven through httpx's ASGITransport (the reassign-chair endpoint).

No network, no API key (the drafter takes its deterministic fallback path).
"""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import main
from app.core import tracing
from app.core.tracing import configure_tracing
from app.db.database import Base, get_db
from app.db.models import Chair, Email
from app.pipeline.drafter import DraftResponse
from app.pipeline.orchestrator import EmailPipeline
from app.repositories.audit_repository import AuditRepository
from app.repositories.email_repository import EmailRepository

# backend/tests/… → parents[2] is the repo root.
_ROOT = Path(__file__).resolve().parents[2]
_DATASET = _ROOT / "data" / "emails" / "toy_multichair.json"

# The five standing chairs, mirroring the migration seed (order fixes ids 1..5).
# Each content chair owns one family of the 14-intent taxonomy (submission
# concepts like publicity / logistics no longer exist as intents, so the two
# non-paper chairs take the leftover review_workflow / committee families).
_SEED_CHAIRS = [
    ("Program Chair", "Program Chair", [
        "author_profile_compliance", "submission_upload_help",
        "submission_requirements", "submission_format_policy", "author_list_change",
    ]),
    ("Diversity & Ethics Chair", "Diversity & Ethics Chair", [
        "review_decision_appeal", "desk_reject_appeal", "anonymity_violation",
    ]),
    ("Local Arrangements Chair", "Local Arrangements Chair", [
        "reviewer_assignment", "review_submission_help", "paper_bidding",
    ]),
    ("Publicity/Sponsorship Chair", "Publicity & Sponsorship Chair", [
        "reviewer_workload_role", "committee_invitation",
    ]),
    ("General Chair", "General Chair", []),
]

email_repo = EmailRepository()
audit_repo = AuditRepository()


def _load_dataset() -> list[dict]:
    with open(_DATASET, encoding="utf-8") as fh:
        return json.load(fh)


@pytest_asyncio.fixture
async def ctx(tmp_path):
    """In-memory DB + seeded chairs + httpx client, sharing one database."""
    original_log_path = tracing._current_log_path
    configure_tracing(tmp_path / "trace.jsonl")  # keep the real log clean

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
            [Chair(name=n, role_title=r, areas=a, active=True) for n, r, a in _SEED_CHAIRS]
        )
        await session.commit()
        rows = (await session.execute(select(Chair))).scalars().all()
        name_to_id = {c.name: c.id for c in rows}

    async def _override_get_db():
        async with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(client=client, factory=factory, name_to_id=name_to_id)

    main.app.dependency_overrides.clear()
    await engine.dispose()
    configure_tracing(original_log_path)


# ---------------------------------------------------------------------------
# Integration: toy email → classify → route → correct chair assigned
# ---------------------------------------------------------------------------
async def test_dataset_routes_each_email_to_the_expected_chair(ctx):
    """Every toy email lands on its expected chair (or no chair when auto-replied)."""
    dataset = _load_dataset()
    pipeline = EmailPipeline()

    async with ctx.factory() as db:
        for e in dataset:
            result = await pipeline.process_email(
                {"from": e["from"], "to": e["to"], "subject": e["subject"], "body": e["body"]},
                db,
            )
            email = await email_repo.get_email_by_id(db, result.email_id)
            lane = result.routing.lane

            # Non-ambiguous emails must classify to their labeled intent.
            if not e.get("ambiguous"):
                assert result.classification.intent == e["expected_intent"], e["id"]

            if lane == "human_review":
                # Human-review emails are assigned to the expected chair.
                expected_id = ctx.name_to_id[e["expected_chair"]]
                assert email.assigned_chair_id == expected_id, e["id"]
            else:
                # FAQ-lane emails are auto-replied and never assigned a chair.
                assert email.assigned_chair_id is None, e["id"]


async def test_all_five_chairs_have_coverage(ctx):
    """The dataset + fallback path together exercise all five chairs.

    The four content chairs are reached by their owned intents; the General
    Chair is reached via the fallback path (an email whose owning chair is
    inactive), proving the empty-areas catch-all works end-to-end.
    """
    pipeline = EmailPipeline()
    reached: set[int] = set()

    async with ctx.factory() as db:
        # Content chairs, via the human-review dataset emails.
        for e in _load_dataset():
            result = await pipeline.process_email(
                {"from": e["from"], "to": e["to"], "subject": e["subject"], "body": e["body"]},
                db,
            )
            email = await email_repo.get_email_by_id(db, result.email_id)
            if email.assigned_chair_id is not None:
                reached.add(email.assigned_chair_id)

    # Program, Diversity & Ethics, Local Arrangements, Publicity/Sponsorship.
    for name in (
        "Program Chair",
        "Diversity & Ethics Chair",
        "Local Arrangements Chair",
        "Publicity/Sponsorship Chair",
    ):
        assert ctx.name_to_id[name] in reached, name

    # General Chair (fallback) — deactivate the appeals/integrity owner so an
    # integrity email falls through to the empty-areas catch-all.
    async with ctx.factory() as db:
        ethics_chair = await db.get(Chair, ctx.name_to_id["Diversity & Ethics Chair"])
        ethics_chair.active = False
        await db.commit()

        result = await pipeline.process_email(
            {"from": "x@u.edu", "subject": "Possible double-blind anonymity violation",
             "body": "I believe a submission breaks double-blind anonymity: it "
                     "includes identifying information that de-anonymizes the authors."},
            db,
        )
        email = await email_repo.get_email_by_id(db, result.email_id)
        assert email.assigned_chair_id == ctx.name_to_id["General Chair"]


async def test_faq_lane_email_has_no_chair(ctx):
    """A confidently-answerable general inquiry is auto-replied, not assigned."""
    pipeline = EmailPipeline()
    async with ctx.factory() as db:
        result = await pipeline.process_email(
            {"from": "a@u.edu", "subject": "Registration and fees",
             "body": "What is the registration fee, and is there a student discount to attend the workshop?"},
            db,
        )
        email = await email_repo.get_email_by_id(db, result.email_id)
        if result.routing.lane == "faq":
            assert email.assigned_chair_id is None


# ---------------------------------------------------------------------------
# Placeholder downgrade: a [CHAIR: ...] draft is never FAQ-complete (Phase 7F)
# ---------------------------------------------------------------------------
class _PlaceholderDrafter:
    """Stub drafter returning a reply that still needs chair input.

    The lane router runs AFTER the drafter and is itself draft-aware (Task
    F2/F3): a non-empty ``placeholders`` list alone is enough to make the
    REAL router pick human_review, whatever the classification looked like.
    So this test exercises the real router (not a stub) to prove that
    end-to-end guarantee, plus the chair assignment that follows from it.
    """

    provider = "stub"

    async def draft(self, email, classification, retrieved_chunks):
        return DraftResponse(
            draft_text="The fee is [CHAIR: confirm the registration fee].",
            placeholders=["confirm the registration fee"],
            model_used="stub",
            generation_metadata={},
        )


async def test_placeholder_draft_downgrades_faq_to_human_review(ctx):
    """Even a high-confidence, grounded email is routed to human review — and
    picks up a chair assignment — when its draft carries [CHAIR: ...]
    placeholders (the real, draft-aware router enforces this, not a
    post-router override)."""
    pipeline = EmailPipeline()
    pipeline.drafter = _PlaceholderDrafter()

    async with ctx.factory() as db:
        result = await pipeline.process_email(
            {"from": "a@u.edu", "subject": "Registration and fees",
             "body": "What is the registration fee for the workshop?"},
            db,
        )
        assert result.routing.lane == "human_review"
        assert "placeholder" in result.routing.reason
        email = await email_repo.get_email_by_id(db, result.email_id)
        assert (email.routing or {}).get("lane") == "human_review"
        assert email.assigned_chair_id is not None


# ---------------------------------------------------------------------------
# Reassignment: updates assigned_chair_id + writes the audit signal
# ---------------------------------------------------------------------------
async def _make_assigned_email(ctx, chair_id: int) -> int:
    """Insert a human-review email already assigned to ``chair_id``; return its id."""
    async with ctx.factory() as db:
        email = Email(
            sender="author@u.edu",
            subject="Anonymity question",
            body="Reporting a possible double-blind anonymity violation.",
            status="ROUTED",
            classification={"intent": "anonymity_violation", "confidence": 0.95},
            routing={"lane": "human_review"},
            assigned_chair_id=chair_id,
        )
        db.add(email)
        await db.commit()
        await db.refresh(email)
        return email.id


async def test_reassign_updates_chair_and_writes_audit(ctx):
    program = ctx.name_to_id["Program Chair"]
    ethics = ctx.name_to_id["Diversity & Ethics Chair"]
    # Start deliberately mis-assigned to the Program Chair.
    email_id = await _make_assigned_email(ctx, program)

    resp = await ctx.client.patch(
        f"/api/v1/emails/{email_id}/reassign-chair",
        json={"reassigned_by": "chair@conf.org", "new_chair_id": ethics,
              "reason": "This is an ethics matter, not a program one."},
    )
    assert resp.status_code == 200
    assert resp.json()["assigned_chair_id"] == ethics

    # assigned_chair_id is persisted.
    async with ctx.factory() as db:
        email = await email_repo.get_email_by_id(db, str(email_id))
        assert email.assigned_chair_id == ethics

        # The audit entry captures the full reroute signal shape.
        trail = await audit_repo.get_audit_trail(db, str(email_id))
        entries = [a for a in trail if a.action == "chair_reassigned"]
        assert len(entries) == 1
        meta = entries[0].extra_metadata
        assert meta["original_chair_id"] == program
        assert meta["new_chair_id"] == ethics
        assert meta["intent"] == "anonymity_violation"
        assert meta["confidence"] == 0.95
        assert entries[0].timestamp is not None  # timestamp is recorded


async def test_reassign_unknown_email_404(ctx):
    resp = await ctx.client.patch(
        "/api/v1/emails/999999/reassign-chair",
        json={"reassigned_by": "c", "new_chair_id": ctx.name_to_id["General Chair"]},
    )
    assert resp.status_code == 404


async def test_reassign_unknown_chair_404(ctx):
    email_id = await _make_assigned_email(ctx, ctx.name_to_id["Program Chair"])
    resp = await ctx.client.patch(
        f"/api/v1/emails/{email_id}/reassign-chair",
        json={"reassigned_by": "c", "new_chair_id": 999999},
    )
    assert resp.status_code == 404
