"""The orchestrator computes an extraction on every pipeline path.

SCOPE LIMIT: subtask 3 wires the extractor into ``_compute`` and holds the
result in memory only. Nothing persists it and no endpoint serves it yet, so
these tests observe the call through the ``pipeline.extractor`` seam rather
than through a stored column or an API response. When persistence lands, the
assertions here stay valid — they pin that the call HAPPENS, with the right
inputs, on every entry point.
"""

from datetime import datetime, timedelta, timezone

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.models import Base
from app.pipeline.distiller import DistillResult
from app.pipeline.extractor import EmailExtractor, ExtractionResult
from app.pipeline.orchestrator import EmailPipeline
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


class _SpyExtractor:
    """Records every call, delegating to the REAL extractor.

    Delegating matters: the assertions below are about genuine extractor
    behaviour reached through the orchestrator, not about a fake that could
    agree with a broken wiring.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._real = EmailExtractor()

    def extract(self, subject, body, sender, sender_name, distilled):
        result = self._real.extract(subject, body, sender, sender_name, distilled)
        self.calls.append(
            {
                "subject": subject,
                "body": body,
                "sender": sender,
                "sender_name": sender_name,
                "distilled": distilled,
                "result": result,
            }
        )
        return result


class _StubDistiller:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def distill(self, subject, body, *, transcript=None):
        self.calls += 1
        return self.result


class _StubRetriever:
    async def retrieve(self, query, intent, top_k=3, *, prior_intent=""):
        return []


def _pipeline(distilled=None, *, stub_distiller=False):
    pipeline = EmailPipeline()
    pipeline.extractor = _SpyExtractor()
    pipeline.retriever = _StubRetriever()
    if stub_distiller:
        pipeline.distiller = _StubDistiller(distilled)
    return pipeline


_EMAIL = {
    "from": "jane@example.edu",
    "sender_name": "Jane Roe",
    "subject": "Re: Desk Rejection of Your Submission 22336",
    "body": "Dear chairs, we would like to appeal this decision.",
}


# ---------------------------------------------------------------------------
# The extraction is computed, on both paths
# ---------------------------------------------------------------------------
async def test_extraction_computed_via_regex_when_distillation_returns_none(session):
    """conftest pins QUERY_STRATEGY=prefix, so no distiller output exists."""
    pipeline = _pipeline()
    await pipeline.process_email(_EMAIL, session)

    assert len(pipeline.extractor.calls) == 1
    call = pipeline.extractor.calls[0]
    assert call["distilled"] is None
    result = call["result"]
    assert result.method == "regex_fallback"
    # The real submission number, read out of the SUBJECT line.
    assert result.submission_number == "22336"
    assert [a.email for a in result.authors] == ["jane@example.edu"]


async def test_extraction_computed_via_llm_path_when_distillation_succeeds(
    session, monkeypatch
):
    monkeypatch.setattr(settings, "QUERY_STRATEGY", "distill")
    pipeline = _pipeline(
        DistillResult(
            queries=["desk rejection appeal procedure"],
            intent="desk_reject_appeal",
            confidence=0.9,
            submission_number_raw="99999",
            openreview_id_raw="Ab3xY9kLm2",
            authors_raw=["Jane Roe | jane@example.edu | Example University"],
        ),
        stub_distiller=True,
    )
    await pipeline.process_email(_EMAIL, session)

    result = pipeline.extractor.calls[0]["result"]
    assert result.method == "llm_distiller"
    # The distiller's answer wins over the 22336 sitting in the subject line —
    # regex is a fallback, never a supplement.
    assert result.submission_number == "99999"
    assert result.openreview_forum_id == "Ab3xY9kLm2"
    assert result.authors[0].affiliation == "Example University"


async def test_extraction_receives_the_subject_and_body_it_needs(session):
    pipeline = _pipeline()
    await pipeline.process_email(_EMAIL, session)

    call = pipeline.extractor.calls[0]
    assert call["subject"] == _EMAIL["subject"]
    assert call["body"] == _EMAIL["body"]
    assert call["sender"] == "jane@example.edu"
    assert call["sender_name"] == "Jane Roe"


# ---------------------------------------------------------------------------
# The sender placeholder must not leak into identity extraction
# ---------------------------------------------------------------------------
async def test_extraction_is_not_given_the_unknown_sender_placeholder(session):
    """`unknown@unknown` is a NOT NULL storage default, not a person.

    Passing it through would fabricate an author for an email that named
    nobody, and would report `regex_fallback` where `none` is the truth.
    """
    pipeline = _pipeline()
    await pipeline.process_email(
        {"subject": "Question", "body": "Can you help?"}, session
    )

    call = pipeline.extractor.calls[0]
    assert call["sender"] == ""
    assert call["result"].method == "none"
    assert call["result"].authors == []


async def test_record_still_stores_the_unknown_sender_placeholder(session):
    """The hoist must not change what is persisted (byte-for-byte guard)."""
    pipeline = _pipeline()
    result = await pipeline.process_email(
        {"subject": "Question", "body": "Can you help?"}, session
    )

    email = await EmailRepository().get_email_by_id(session, result.email_id)
    assert email.sender == "unknown@unknown"
    assert email.sender_name is None


async def test_record_sender_precedence_from_and_sender_keys(session):
    """`from` wins over `sender`, and both still reach the extractor raw."""
    pipeline = _pipeline()
    result = await pipeline.process_email(
        {"from": "a@x.edu", "sender": "b@x.edu", "subject": "S", "body": "B"}, session
    )
    email = await EmailRepository().get_email_by_id(session, result.email_id)
    assert email.sender == "a@x.edu"
    assert pipeline.extractor.calls[0]["sender"] == "a@x.edu"

    pipeline2 = _pipeline()
    result2 = await pipeline2.process_email(
        {"sender": "b@x.edu", "subject": "S", "body": "B"}, session
    )
    email2 = await EmailRepository().get_email_by_id(session, result2.email_id)
    assert email2.sender == "b@x.edu"
    assert pipeline2.extractor.calls[0]["sender"] == "b@x.edu"


# ---------------------------------------------------------------------------
# Every entry point funnels through _compute, so all of them extract
# ---------------------------------------------------------------------------
async def test_extraction_runs_on_the_public_compute_seam(session):
    """`compute` is what per-follow-up processing uses (_process_followup_messages)."""
    pipeline = _pipeline()
    await pipeline.compute(_EMAIL, session)

    assert len(pipeline.extractor.calls) == 1
    assert pipeline.extractor.calls[0]["result"].submission_number == "22336"


async def test_extraction_runs_on_reprocess_email(session):
    pipeline = _pipeline()
    created = await pipeline.process_email(_EMAIL, session)
    email = await EmailRepository().get_email_by_id(session, created.email_id)

    await pipeline.reprocess_email(session, email)

    assert len(pipeline.extractor.calls) == 2  # once to create, once to redraft
    assert pipeline.extractor.calls[1]["result"].submission_number == "22336"


async def test_extraction_runs_on_thread_followup_reprocess(session):
    """The LIVE follow-up path (the poller's), which reprocesses over a thread."""
    pipeline = _pipeline()
    created = await pipeline.process_email(_EMAIL, session)
    email = await EmailRepository().get_email_by_id(session, created.email_id)

    now = datetime.now(timezone.utc)
    messages = [
        {
            "public": True,
            "author_id": 1,
            "author_role": "end-user",
            "plain_body": "Following up on submission 44444, any news?",
            "created_at": now,
        },
        {
            "public": True,
            "author_id": 2,
            "author_role": "agent",
            "plain_body": "We are looking into it.",
            "created_at": now - timedelta(minutes=5),
        },
    ]
    await pipeline.reprocess_email_with_thread(session, email, messages)

    assert len(pipeline.extractor.calls) == 2
    call = pipeline.extractor.calls[1]
    # The follow-up path swaps the body for the latest requester turn, so the
    # extractor sees the CURRENT ask, not the original inquiry.
    assert "44444" in call["body"]
    assert call["subject"] == _EMAIL["subject"]
    # ⚠️ BEHAVIOUR CHANGE — this assertion was "22336" until the quoted-
    # notification tie-break landed, and the comment here used to argue that the
    # ticket's subject identifies the submission the whole thread is about, so a
    # follow-up could not retarget it.
    #
    # This fixture's subject ("Re: Desk Rejection of Your Submission 22336") is
    # exactly the shape the tie-break targets — a reply marker plus a
    # notification phrase — and the follow-up names a different submission. So
    # the rule now fires on the THREAD path too, not just within one email, and
    # the current turn wins.
    #
    # Defensible: a requester writing "following up on submission 44444" is
    # asking about 44444. But it is a wider consequence than the tie-break was
    # scoped for, so it is flagged rather than quietly absorbed. To restore the
    # old behaviour, gate the tie-break off when a thread transcript is present.
    assert call["result"].submission_number == "44444"


# ---------------------------------------------------------------------------
# Purely additive: nothing existing changes
# ---------------------------------------------------------------------------
async def test_extraction_adds_no_model_call(session, monkeypatch):
    """The extractor reuses the distiller output; it never triggers a second call."""
    monkeypatch.setattr(settings, "QUERY_STRATEGY", "distill")
    pipeline = _pipeline(
        DistillResult(queries=["q"], intent="cms_support", confidence=0.8),
        stub_distiller=True,
    )
    await pipeline.process_email(_EMAIL, session)

    assert pipeline.distiller.calls == 1


async def test_classification_routing_and_draft_are_unchanged(session):
    """The pipeline's own outputs must be untouched by the wiring."""
    pipeline = _pipeline()
    result = await pipeline.process_email(_EMAIL, session)

    email = await EmailRepository().get_email_by_id(session, result.email_id)
    assert email.classification is not None
    assert email.classification["intent"] == result.classification.intent
    assert email.routing["lane"] == result.routing.lane
    assert email.draft["draft_text"] == result.draft.draft_text
    assert email.retrieval_context is not None
    # The extraction is held in memory only in this subtask — it must NOT have
    # leaked into any persisted column.
    for column in (email.classification, email.routing, email.draft,
                   email.retrieval_context):
        assert "submission_number" not in column
        assert "extraction" not in column


async def test_extraction_result_is_the_documented_type(session):
    pipeline = _pipeline()
    await pipeline.process_email(_EMAIL, session)
    assert isinstance(pipeline.extractor.calls[0]["result"], ExtractionResult)


async def test_pipeline_owns_a_real_extractor_by_default():
    """The seam exists on a stock pipeline, not only when a test injects one."""
    assert isinstance(EmailPipeline().extractor, EmailExtractor)
