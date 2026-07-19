"""The re-evaluate-open-tickets sweep: gate + re-draft + audit + flag."""

from contextlib import asynccontextmanager

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Email
from app.models.enums import EmailStatus
from app.pipeline.reevaluation import reevaluate_open_tickets
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
    """Returns a fixed id list regardless of query, to drive the gate."""

    def __init__(self, ids):
        from app.pipeline.retriever import RetrievedChunk
        self._chunks = [
            RetrievedChunk(policy_id=i, title=i, content=f"body {i}", score=1.0)
            for i in ids
        ]

    async def retrieve(self, query, intent, top_k=3):
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
