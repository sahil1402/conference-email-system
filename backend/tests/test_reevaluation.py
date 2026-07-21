"""The re-evaluate-open-tickets sweep: gate + re-draft + audit + flag."""

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Email
from app.models.enums import EmailSource, EmailStatus
from app.pipeline.drafter import DraftResponse
from app.pipeline.reevaluation import reevaluate_open_tickets
from app.pipeline.router import LANE_FAQ, LANE_HUMAN_REVIEW, RoutingDecision
from app.repositories.audit_repository import AuditRepository
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


def _factory(session):
    """A session_factory that yields the test's OWN session (so the sweep's
    writes are visible to the assertions) and never closes it."""

    @asynccontextmanager
    async def factory():
        yield session

    return factory


def _open_email(**ctx_over) -> Email:
    """An open ticket with a stored draft + retrieval context."""
    ctx = {"query": "deadline extension", "intent": "", "retrieved_ids": ["policy_101"]}
    ctx.update(ctx_over)
    return Email(
        sender="a@b.com",
        subject="Deadline?",
        body="Can I get a deadline extension?",
        status=EmailStatus.DRAFT_GENERATED.value,
        classification={"intent": "deadlines", "confidence": 0.6,
                        "reasoning": "x", "method": "keyword"},
        routing={"lane": "human_review", "reason": "x"},
        draft={"draft_text": "old", "notes_for_chair": None, "placeholders": [],
               "citations": ["policy_101"], "model_used": "fallback",
               "generation_metadata": {}},
        retrieval_context=ctx,
    )


class _StubRetriever:
    """Returns a fixed id list regardless of query, to drive the gate.

    Optional ``intents`` (parallel to ``ids``) let a test hold the id-set fixed
    while changing a chunk's intents — exercising the chunk-hash axis of the gate.
    """

    def __init__(self, ids, intents=None):
        from app.pipeline.retriever import RetrievedChunk
        intents = intents or [[] for _ in ids]
        self._chunks = [
            RetrievedChunk(
                policy_id=i, title=i, content=f"body {i}", score=1.0, intents=ints
            )
            for i, ints in zip(ids, intents)
        ]

    async def retrieve(self, query, intent, top_k=3, *, prior_intent=""):
        return self._chunks[:top_k]


async def test_unaffected_ticket_is_not_redrafted(session, monkeypatch):
    session.add(_open_email())
    await session.commit()

    # Fresh retrieval returns the SAME id set as stored → not affected.
    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_101"])
    )
    stats = await reevaluate_open_tickets(session_factory=_factory(session))
    assert stats == {
        "open": 1, "redrafted": 0, "skipped_edited": 0,
        "skipped_no_context": 0, "skipped_contended": 0, "unaffected": 1,
    }


async def test_affected_ticket_is_redrafted_and_context_updated(session, monkeypatch):
    session.add(_open_email())
    await session.commit()

    # Fresh retrieval surfaces a different policy → affected → re-draft.
    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_999"])
    )
    stats = await reevaluate_open_tickets(session_factory=_factory(session))
    assert stats["redrafted"] == 1

    email = (await EmailRepository().get_open_tickets(session))[0]
    assert email.retrieval_context["retrieved_ids"] == ["policy_999"]
    assert email.redrafting is False
    trail = await AuditRepository().get_audit_trail(session, str(email.id))
    assert any(a.action == "ticket_redrafted" for a in trail)


async def test_chair_edited_ticket_is_skipped(session, monkeypatch):
    e = _open_email()
    e.draft = {**e.draft, "is_edited": True}
    session.add(e)
    await session.commit()

    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_999"])
    )
    stats = await reevaluate_open_tickets(session_factory=_factory(session))
    assert stats["skipped_edited"] == 1
    assert stats["redrafted"] == 0

    email = (await EmailRepository().get_open_tickets(session))[0]
    assert email.draft["draft_text"] == "old"  # untouched
    trail = await AuditRepository().get_audit_trail(session, str(email.id))
    assert any(a.action == "ticket_redraft_skipped_edited" for a in trail)


async def test_repeat_sweep_is_noop_after_redraft(session, monkeypatch):
    session.add(_open_email())
    await session.commit()
    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_999"])
    )
    first = await reevaluate_open_tickets(session_factory=_factory(session))
    assert first["redrafted"] == 1
    # Second sweep: stored ids now equal fresh ids → nothing to do.
    second = await reevaluate_open_tickets(session_factory=_factory(session))
    assert second["redrafted"] == 0
    assert second["unaffected"] == 1


async def test_null_context_ticket_is_skipped(session, monkeypatch):
    e = _open_email()
    e.retrieval_context = None                 # legacy row: never captured
    session.add(e)
    await session.commit()

    # Even though the stub retriever would return ids, a null-context ticket must
    # NOT be re-drafted (would clobber its draft onto arbitrary grounding).
    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_999"])
    )
    stats = await reevaluate_open_tickets(session_factory=_factory(session))
    assert stats["skipped_no_context"] == 1
    assert stats["redrafted"] == 0
    reloaded = (await EmailRepository().get_open_tickets(session))[0]
    assert reloaded.draft["draft_text"] == "old"   # untouched


async def test_already_redrafting_ticket_is_contended(session, monkeypatch):
    e = _open_email()
    e.redrafting = True                        # as if another sweep claimed it
    session.add(e)
    await session.commit()

    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_999"])
    )
    stats = await reevaluate_open_tickets(session_factory=_factory(session))
    assert stats["skipped_contended"] == 1
    assert stats["redrafted"] == 0
    reloaded = (await EmailRepository().get_open_tickets(session))[0]
    assert reloaded.draft["draft_text"] == "old"


async def test_draft_failure_clears_flag_and_continues(session, monkeypatch):
    session.add(_open_email())
    await session.commit()

    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_999"])
    )

    class _RaisingDrafter:
        def __init__(self, *a, **k):
            pass

        async def draft(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr("app.pipeline.reevaluation.ResponseDrafter", _RaisingDrafter)

    stats = await reevaluate_open_tickets(session_factory=_factory(session))
    assert stats["redrafted"] == 0
    reloaded = (await EmailRepository().get_open_tickets(session))[0]
    assert reloaded.redrafting is False        # flag cleared despite the failure
    assert reloaded.draft["draft_text"] == "old"


async def test_clear_stale_redrafting_flags(session):
    from app.pipeline.reevaluation import clear_stale_redrafting_flags

    stuck = _open_email()
    stuck.redrafting = True
    session.add(stuck)
    normal = _open_email()
    session.add(normal)
    await session.commit()

    cleared = await clear_stale_redrafting_flags(session_factory=_factory(session))
    assert cleared == 1
    rows = await EmailRepository().get_open_tickets(session)
    assert all(r.redrafting is False for r in rows)


async def test_empty_retrieved_ids_with_context_stays_eligible(session, monkeypatch):
    # A real ingest that matched nothing (context PRESENT, retrieved_ids empty) MUST
    # stay eligible: a later KB addition that now surfaces a policy should re-draft it.
    # Guards the I1 discriminator (retrieval_context is None), NOT "not retrieved_ids".
    e = _open_email(retrieved_ids=[])
    session.add(e)
    await session.commit()

    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_777"])
    )
    stats = await reevaluate_open_tickets(session_factory=_factory(session))
    assert stats["redrafted"] == 1
    assert stats["skipped_no_context"] == 0
    reloaded = (await EmailRepository().get_open_tickets(session))[0]
    assert reloaded.retrieval_context["retrieved_ids"] == ["policy_777"]


async def test_intent_relabel_fires_redraft_via_hash(session, monkeypatch):
    # Same top-k id SET, but the grounded chunk's intents changed (a chair re-labelled
    # the KB). The id-set gate alone calls this unaffected; the (id, intents) chunk-hash
    # gate must notice and fire a re-draft. The stored hash reflects the OLD intents.
    from app.pipeline.retriever import RetrievedChunk, grounded_chunks_hash

    old = [RetrievedChunk(policy_id="policy_101", title="t", content="c", score=1.0,
                          intents=["submission_requirements"])]
    e = _open_email(retrieved_ids=["policy_101"], chunk_hash=grounded_chunks_hash(old))
    session.add(e)
    await session.commit()

    # Fresh retrieval: SAME id, DIFFERENT intents → hash differs.
    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever",
        lambda: _StubRetriever(["policy_101"], intents=[["submission_format_policy"]]),
    )
    stats = await reevaluate_open_tickets(session_factory=_factory(session))
    assert stats["redrafted"] == 1
    assert stats["unaffected"] == 0

    reloaded = (await EmailRepository().get_open_tickets(session))[0]
    # Id-set unchanged, but context (and its hash) refreshed to the new intents.
    assert reloaded.retrieval_context["retrieved_ids"] == ["policy_101"]
    assert reloaded.retrieval_context["chunk_hash"] == grounded_chunks_hash(
        [RetrievedChunk(policy_id="policy_101", title="t", content="c", score=1.0,
                        intents=["submission_format_policy"])]
    )


async def test_legacy_row_without_hash_does_not_redraft_on_relabel(session, monkeypatch):
    # Legacy row: retrieval_context PRESENT but with NO chunk_hash. Even if the fresh
    # chunk's intents differ, with no stored hash to compare the sweep must NOT
    # spuriously re-draft — the id-set gate still governs (here the id-set is stable).
    e = _open_email(retrieved_ids=["policy_101"])   # default ctx has no chunk_hash
    assert "chunk_hash" not in e.retrieval_context
    session.add(e)
    await session.commit()

    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever",
        lambda: _StubRetriever(["policy_101"], intents=[["anything_else"]]),
    )
    stats = await reevaluate_open_tickets(session_factory=_factory(session))
    assert stats["unaffected"] == 1
    assert stats["redrafted"] == 0
    reloaded = (await EmailRepository().get_open_tickets(session))[0]
    assert reloaded.draft["draft_text"] == "old"    # untouched


async def test_batch_marked_before_any_redraft(session, monkeypatch):
    # Two-pass sweep: BOTH affected tickets must be marked "re-drafting" (claim +
    # ticket_redrafting audit) BEFORE either is re-drafted, so the queue shows the
    # whole batch in-progress and then resolves one by one. We assert the audit
    # ordering: every ticket_redrafting precedes every ticket_redrafted.
    from sqlalchemy import select

    from app.db.models import AuditLog

    session.add(_open_email())
    session.add(_open_email())
    await session.commit()
    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_999"])
    )

    stats = await reevaluate_open_tickets(session_factory=_factory(session))
    assert stats["redrafted"] == 2

    actions = [
        r.action
        for r in (await session.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
    ]
    assert actions.count("ticket_redrafting") == 2
    assert actions.count("ticket_redrafted") == 2
    last_claim = len(actions) - 1 - actions[::-1].index("ticket_redrafting")
    first_done = actions.index("ticket_redrafted")
    assert last_claim < first_done, f"expected all claims before any redraft: {actions}"


async def test_save_refusal_mid_sweep_clears_flag_and_contends(session, monkeypatch):
    # Simulate the chair approving between claim and save: save_redraft refuses (0-row
    # conditional UPDATE). The sweep must clear the stray flag it set and count the
    # ticket as contended, never as redrafted, and never clobber the draft.
    session.add(_open_email())
    await session.commit()

    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_999"])
    )

    async def _refuse(*a, **k):
        return None

    monkeypatch.setattr(EmailRepository, "save_redraft", _refuse)

    stats = await reevaluate_open_tickets(session_factory=_factory(session))
    assert stats["redrafted"] == 0
    assert stats["skipped_contended"] == 1
    reloaded = (await EmailRepository().get_open_tickets(session))[0]
    assert reloaded.redrafting is False        # stray flag cleared
    assert reloaded.draft["draft_text"] == "old"


class _AlwaysFaqRouter:
    """Stub router that routes to "faq" no matter what — draft-blind, exactly
    like the RL strategy the safety floor exists to cover."""

    def __init__(self, strategy=None):
        pass

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

    def __init__(self, provider=None):
        pass

    async def draft(self, email_data, classification, retrieved_chunks):
        return DraftResponse(
            draft_text="Thanks for reaching out. [CHAIR: confirm exact date].",
            notes_for_chair=None,
            placeholders=["[CHAIR: confirm exact date]"],
            citations=["policy_101"],
            answer_confidence=0.95,
            model_used="stub",
        )


async def test_pass2_safety_floor_overrides_faq_when_draft_has_placeholders(
    session, monkeypatch
):
    """Same strategy-independent floor as the orchestrator's, exercised on the
    Pass-2 re-draft path: even when the (stubbed) router says "faq" for a
    draft that still has an unresolved placeholder, the saved routing must be
    forced to human_review."""
    session.add(_open_email())
    await session.commit()

    # Fresh retrieval surfaces a different policy → affected → re-draft.
    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_999"])
    )
    monkeypatch.setattr("app.pipeline.reevaluation.EmailRouter", _AlwaysFaqRouter)
    monkeypatch.setattr("app.pipeline.reevaluation.ResponseDrafter", _PlaceholderDrafter)

    stats = await reevaluate_open_tickets(session_factory=_factory(session))
    assert stats["redrafted"] == 1

    reloaded = (await EmailRepository().get_open_tickets(session))[0]
    assert reloaded.routing["lane"] == LANE_HUMAN_REVIEW
    assert reloaded.routing.get("override_reason") is not None
    assert "placeholder" in reloaded.routing["override_reason"]


# --- Resolved-Zendesk-status guard on get_open_tickets ----------------------
# A ticket already solved/closed in Zendesk must not be re-drafted by a policy
# sweep, even while its LOCAL status is still draft_generated (the two statuses
# are orthogonal). Scoped to the sweep only: a NEW customer comment on a solved
# ticket still reprocesses via the ingest adapter's follow-up path.


def _zendesk_email(zendesk_status: str | None) -> Email:
    """An open ticket carrying a Zendesk status (None = non-Zendesk row)."""
    email = _open_email()
    email.source = (
        EmailSource.ZENDESK.value
        if zendesk_status is not None
        else EmailSource.TOY_DATASET.value
    )
    email.zendesk_status = zendesk_status
    return email


@pytest.mark.parametrize("resolved_status", ["solved", "closed"])
async def test_resolved_zendesk_ticket_excluded_from_open_tickets(
    session, resolved_status
):
    """solved/closed in Zendesk → never returned to the sweep."""
    session.add(_zendesk_email(resolved_status))
    await session.commit()

    assert await EmailRepository().get_open_tickets(session) == []


@pytest.mark.parametrize("active_status", ["new", "open", "pending", "hold"])
async def test_active_zendesk_ticket_still_included(session, active_status):
    """Every non-resolved Zendesk status is swept exactly as before."""
    session.add(_zendesk_email(active_status))
    await session.commit()

    tickets = await EmailRepository().get_open_tickets(session)
    assert [t.zendesk_status for t in tickets] == [active_status]


async def test_null_zendesk_status_still_included(session):
    """Regression guard for the NULL handling.

    Non-Zendesk rows (toy_dataset) have zendesk_status NULL. SQL three-valued
    logic makes ``NULL NOT IN ('solved','closed')`` evaluate to NULL rather than
    TRUE, so a bare NOT IN would silently drop EVERY non-Zendesk ticket from the
    sweep. This fails if the explicit IS NULL branch is ever removed.
    """
    session.add(_zendesk_email(None))
    await session.commit()

    tickets = await EmailRepository().get_open_tickets(session)
    assert len(tickets) == 1
    assert tickets[0].zendesk_status is None


async def test_sweep_skips_resolved_but_sweeps_active(session, monkeypatch):
    """End-to-end through the sweep, not just the query.

    Three tickets differing ONLY in zendesk_status, with retrieval stubbed to
    shift the grounding set so every swept ticket WOULD be re-drafted. Only the
    active one is, proving the guard gates real model spend rather than merely
    filtering a list.
    """
    for status_value in ("solved", "closed", "open"):
        session.add(_zendesk_email(status_value))
    await session.commit()

    monkeypatch.setattr(
        "app.pipeline.reevaluation.get_retriever", lambda: _StubRetriever(["policy_999"])
    )
    stats = await reevaluate_open_tickets(session_factory=_factory(session))

    assert stats["open"] == 1
    assert stats["redrafted"] == 1

    swept = await EmailRepository().get_open_tickets(session)
    assert [t.zendesk_status for t in swept] == ["open"]
    # The resolved pair kept their original draft — untouched by the sweep.
    resolved = (
        await session.execute(
            select(Email).where(Email.zendesk_status.in_(("solved", "closed")))
        )
    ).scalars().all()
    assert len(resolved) == 2
    assert all(t.draft["draft_text"] == "old" for t in resolved)
