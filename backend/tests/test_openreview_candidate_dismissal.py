"""Dismissing a false-positive OpenReview reply candidate.

The detection is text-based: an email carrying both a note id and a venue
notification address is treated as a reply to that notification. A chair reading
the email is the authority on whether it actually is one, and this endpoint
records that judgment.

⚠️ THE CENTRAL TEST IN THIS FILE IS ``test_the_dismissal_survives_reprocessing``.
Every other assertion here is also satisfied by the naive implementation —
writing ``openreview_reply_candidate: false`` into the ``extraction`` JSON —
which passes a fresh queue query and then silently reverts the first time the
email is reprocessed, because ``orchestrator._compute`` overwrites that column
wholesale with a freshly computed ``ExtractionResult.model_dump()``. Only the
reprocessing test separates the two designs, and it is mutation-proven to do so.
"""

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import main
from app.api.v1 import emails as emails_module
from app.db.database import get_db
from app.db.models import AuditLog, Base, Email
from app.pipeline.extractor import EmailExtractor
from app.repositories.email_repository import EmailRepository

QUEUE = "/api/v1/emails/queue"
OR_QUEUE = "/api/v1/emails/queue/openreview"
URL = "/api/v1/emails/{id}/dismiss-openreview-candidate"

NOTE_ID = "jnHgRMHgrm"
SENDER_ADDR = "aaai2027-notifications@openreview.net"

#: A body that the REAL extractor reads as a candidate — both signals present in
#: the quoted notification below the reply. Used by the reprocessing test so the
#: recomputation is genuine rather than a hand-written dict.
CANDIDATE_BODY = (
    "Thank you — I will submit my review by Friday.\n"
    "\n"
    "-----Original Message-----\n"
    f'From: "AAAI 2027" <{SENDER_ADDR}>\n'
    "Subject: SPC commented on a paper you are reviewing\n"
    "\n"
    "Please see: https://openreview.net/forum?id=ll0avn6ylq&noteId="
    f"{NOTE_ID}\n"
)


def _candidate(value: bool = True) -> dict:
    """An extraction dict exactly as the pipeline persists it."""
    return {
        "submission_numbers": ["1030"],
        "openreview_forum_ids": ["ll0avn6ylq"],
        "openreview_note_id": NOTE_ID if value else None,
        "openreview_notification_sender": SENDER_ADDR if value else None,
        "extracted_reply_text": "I will review before the deadline.",
        "authors": [],
        "method": "llm_distiller",
        "openreview_reply_candidate": value,
    }


@pytest.fixture(autouse=True)
def _no_openreview_network(monkeypatch):
    """⚠️ MANDATORY, AND NOT DEFENSIVE — without it this file hits the internet.

    ``GET /emails/{id}`` fetches the parent note's readers LIVE from OpenReview
    for any reply candidate (commit 15b), and every email in this file is one.
    With real credentials present in the environment the endpoint genuinely logs
    in: an early run of this file was caught doing so by a 429 RateLimitError
    from ``api2.dev.openreview.net`` in the captured logs.

    Failing the client factory keeps the lookup entirely in-process — the helper
    catches broadly and reports the ``failed`` state, which is irrelevant to
    everything tested here.
    """

    def _no_client(*_args, **_kwargs):
        raise RuntimeError("OpenReview access is blocked in tests")

    monkeypatch.setattr(emails_module, "get_openreview_client", _no_client)


@pytest_asyncio.fixture
async def client_and_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, factory
    main.app.dependency_overrides.clear()
    await engine.dispose()


async def _seed(factory, **overrides) -> str:
    fields = {
        "sender": "reviewer@example.edu",
        "subject": "Re: [AAAI 2027] SPC commented on a paper",
        "body": CANDIDATE_BODY,
        "status": "DRAFT_GENERATED",
        "routing": {"lane": "human_review", "reason": "sensitive"},
        "classification": {"intent": "review_process", "confidence": 0.8},
        "draft": {"draft_text": "some AI draft"},
        "extraction": _candidate(True),
    }
    fields.update(overrides)
    async with factory() as session:
        email = Email(**fields)
        session.add(email)
        await session.commit()
        await session.refresh(email)
        return str(email.id)


async def _dismiss(client, email_id, **overrides):
    body = {"reason": "Quoted an old notification; not a reply to it.",
            "dismissed_by": "chair1"}
    body.update(overrides)
    return await client.post(URL.format(id=email_id), json=body)


async def _subjects(client, url) -> list[str]:
    resp = await client.get(url, params={"limit": 100})
    assert resp.status_code == 200
    return [e["subject"] for e in resp.json()["emails"]]


async def _row(factory, email_id: str) -> Email:
    async with factory() as session:
        return (
            await session.execute(select(Email).where(Email.id == int(email_id)))
        ).scalars().one()


async def _audit(factory, email_id: str) -> list[AuditLog]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(AuditLog).where(AuditLog.email_id == int(email_id))
                )
            ).scalars().all()
        )


# ---------------------------------------------------------------------------
# The queue effect
# ---------------------------------------------------------------------------
async def test_a_candidate_starts_in_the_openreview_queue(client_and_factory):
    client, factory = client_and_factory
    await _seed(factory, subject="fp")

    assert "fp" in await _subjects(client, OR_QUEUE)
    assert "fp" not in await _subjects(client, QUEUE)


async def test_dismissal_moves_it_to_the_main_queue(client_and_factory):
    client, factory = client_and_factory
    email_id = await _seed(factory, subject="fp")

    resp = await _dismiss(client, email_id)

    assert resp.status_code == 200
    assert "fp" not in await _subjects(client, OR_QUEUE)
    assert "fp" in await _subjects(client, QUEUE)


async def test_the_two_queues_stay_exact_complements(client_and_factory):
    """A dismissed email must land in exactly one queue, like every other row.

    The main queue negates the same predicate the OpenReview queue asserts, so a
    new clause added to only one side would strand a row in neither — invisible
    rather than merely misfiled.
    """
    client, factory = client_and_factory
    await _seed(factory, subject="kept")
    dismissed = await _seed(factory, subject="dropped")
    await _dismiss(client, dismissed)

    main_q = await _subjects(client, QUEUE)
    or_q = await _subjects(client, OR_QUEUE)

    for subject in ("kept", "dropped"):
        assert (subject in main_q) != (subject in or_q), subject


async def test_a_dismissed_email_keeps_its_lane_and_is_filterable_by_it(
    client_and_factory,
):
    """It resumes being an ordinary email — nothing special about it downstream."""
    client, factory = client_and_factory
    email_id = await _seed(factory, subject="fp")

    await _dismiss(client, email_id)

    resp = await client.get(QUEUE, params={"lane": "human_review", "limit": 100})
    assert "fp" in [e["subject"] for e in resp.json()["emails"]]


# ---------------------------------------------------------------------------
# ⚠️ THE CRITICAL REGRESSION TEST
# ---------------------------------------------------------------------------
async def test_the_dismissal_survives_reprocessing(client_and_factory):
    """A reprocess recomputes the candidate flag; the dismissal must outlive it.

    This is the exact failure mode that made storing the dismissal inside
    ``extraction`` wrong, so it is proven rather than argued:

    1. The REAL ``EmailExtractor`` runs over the email's own body and is asserted
       to report ``openreview_reply_candidate = True`` — the recomputation is
       genuine, not a hand-written dict that happens to say True.
    2. That fresh result is persisted through ``update_email_outputs``, which is
       the exact call ``reprocess_email`` makes with ``_compute``'s record
       (orchestrator.py: ``await self.email_repo.update_email_outputs(db,
       str(email.id), c.record)``). Driving the persistence step directly keeps
       the test hermetic — no classifier, retriever or drafter — while exercising
       the write that does the damage.
    3. The email must STILL be out of the OpenReview queue.

    Under the naive implementation step 2 overwrites the dismissal and the email
    reappears. Mutation-verified.
    """
    client, factory = client_and_factory
    email_id = await _seed(factory, subject="fp")
    await _dismiss(client, email_id)
    assert "fp" not in await _subjects(client, OR_QUEUE)

    # 1. Real extraction over the real body.
    row = await _row(factory, email_id)
    # `distilled=None` takes the regex fallback path — no model call, and the
    # path a hermetic reprocess would take anyway under the test conftest's
    # forced fallback provider.
    fresh = EmailExtractor().extract(
        subject=row.subject,
        body=row.body,
        sender=row.sender,
        sender_name=row.sender_name,
        distilled=None,
    )
    recomputed = fresh.model_dump()
    assert recomputed["openreview_reply_candidate"] is True, (
        "fixture body must still recompute as a candidate, or this test proves "
        "nothing"
    )

    # 2. The pipeline's own persistence step.
    async with factory() as session:
        await EmailRepository().update_email_outputs(
            session, email_id, {"extraction": recomputed}
        )

    # 3. The dismissal holds.
    assert "fp" not in await _subjects(client, OR_QUEUE)
    assert "fp" in await _subjects(client, QUEUE)

    after = await _row(factory, email_id)
    assert after.openreview_candidate_dismissed is True
    # And the extraction genuinely was rewritten — so the guard above is what
    # kept the email out, not a failed write that made the test vacuous.
    assert after.extraction["openreview_reply_candidate"] is True


# ---------------------------------------------------------------------------
# What it does NOT touch
# ---------------------------------------------------------------------------
async def test_routing_and_status_are_untouched(client_and_factory):
    """The lane was never the thing that was wrong."""
    client, factory = client_and_factory
    email_id = await _seed(factory, subject="fp")
    before = await _row(factory, email_id)

    await _dismiss(client, email_id)

    after = await _row(factory, email_id)
    assert after.routing == before.routing == {"lane": "human_review",
                                               "reason": "sensitive"}
    assert after.status == before.status == "DRAFT_GENERATED"
    assert after.draft == before.draft


async def test_no_rl_feedback_and_no_active_learning_fire(
    client_and_factory, monkeypatch
):
    """⚠️ ASSERTED AT THE SOURCE, not by "we didn't call the wrapper".

    ``/reroute`` penalises the ``(intent, lane)`` RL arm and feeds active
    learning on classifier confidence. Both would be WRONG here: a false-positive
    OpenReview detection says nothing about the lane router or the classifier,
    which may both have been correct on this email.

    The counters are installed on ``get_rl_router`` and ``build_flag_events``
    themselves — the functions the wrappers delegate to — so an indirect trigger
    through any other call in the write path is caught too, not just a literal
    call to ``_record_rl_feedback``. The audit assertion is the behavioural
    backstop: ``_record_flag_events`` writes audit rows, so the exact action set
    proves it stayed silent regardless of any mock.
    """
    calls = {"rl": 0, "al": 0}

    def _rl(*_a, **_k):
        calls["rl"] += 1
        raise AssertionError("RL router must not be consulted by a dismissal")

    def _al(*_a, **_k):
        calls["al"] += 1
        raise AssertionError("active-learning flagging must not run on a dismissal")

    monkeypatch.setattr(emails_module, "get_rl_router", _rl)
    monkeypatch.setattr(emails_module, "build_flag_events", _al)

    client, factory = client_and_factory
    email_id = await _seed(factory, subject="fp")

    resp = await _dismiss(client, email_id)

    assert resp.status_code == 200
    assert calls == {"rl": 0, "al": 0}
    actions = [a.action for a in await _audit(factory, email_id)]
    assert actions == ["openreview_candidate_dismissed"]
    assert not [a for a in actions if a.startswith("flagged_")]


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "extraction, why",
    [
        (_candidate(False), "examined, not a candidate"),
        (None, "never examined (extraction is NULL)"),
        ({}, "empty extraction record"),
    ],
)
async def test_a_non_candidate_is_rejected_not_silently_accepted(
    client_and_factory, extraction, why
):
    """There is nothing to dismiss, and saying "done" would be a lie."""
    client, factory = client_and_factory
    email_id = await _seed(factory, subject="plain", extraction=extraction)

    resp = await _dismiss(client, email_id)

    assert resp.status_code == 409, why
    assert "nothing to dismiss" in resp.json()["detail"]["message"]
    assert (await _row(factory, email_id)).openreview_candidate_dismissed is False
    assert await _audit(factory, email_id) == []


async def test_a_second_dismissal_is_a_quiet_no_op(client_and_factory):
    """Idempotent, and it does not double-audit.

    The audit log records state CHANGES; two entries would read as two separate
    chair decisions when the row was already in that state. The response says
    which happened so a caller need not guess.
    """
    client, factory = client_and_factory
    email_id = await _seed(factory, subject="fp")

    first = await _dismiss(client, email_id)
    second = await _dismiss(client, email_id)

    assert first.status_code == second.status_code == 200
    assert first.json()["already_dismissed"] is False
    assert second.json()["already_dismissed"] is True
    assert [a.action for a in await _audit(factory, email_id)] == [
        "openreview_candidate_dismissed"
    ]
    assert "fp" not in await _subjects(client, OR_QUEUE)


async def test_the_audit_entry_records_who_why_and_what_was_detected(
    client_and_factory,
):
    client, factory = client_and_factory
    email_id = await _seed(factory, subject="fp")

    await _dismiss(client, email_id, reason="Subject quotes an old notification.")

    (entry,) = await _audit(factory, email_id)
    assert entry.action == "openreview_candidate_dismissed"
    assert entry.actor == "chair1"
    assert entry.extra_metadata["reason"] == "Subject quotes an old notification."
    # The signals that caused the false positive — the feedback the detection
    # logic would be tuned against.
    assert entry.extra_metadata["openreview_note_id"] == NOTE_ID
    assert entry.extra_metadata["openreview_notification_sender"] == SENDER_ADDR
    # Recorded but NOT changed, so a later reader can see it was left alone.
    assert entry.extra_metadata["lane"] == "human_review"


async def test_an_empty_reason_is_rejected(client_and_factory):
    """The reason is the feedback signal for the detection logic."""
    client, factory = client_and_factory
    email_id = await _seed(factory, subject="fp")

    resp = await _dismiss(client, email_id, reason="")

    assert resp.status_code == 422
    assert (await _row(factory, email_id)).openreview_candidate_dismissed is False


async def test_an_unknown_email_is_404(client_and_factory):
    client, _ = client_and_factory

    assert (await _dismiss(client, "999999")).status_code == 404


async def test_the_flag_is_serialized_for_clients(client_and_factory):
    client, factory = client_and_factory
    email_id = await _seed(factory, subject="fp")

    before = await client.get(f"/api/v1/emails/{email_id}")
    await _dismiss(client, email_id)
    after = await client.get(f"/api/v1/emails/{email_id}")

    assert before.json()["email"]["openreview_candidate_dismissed"] is False
    assert after.json()["email"]["openreview_candidate_dismissed"] is True


async def test_dismissal_does_not_hide_the_email_from_analytics(client_and_factory):
    """The tri-state queue mode's default (no opinion) must stay unfiltered.

    Analytics counts over the same query helper; a dismissal is a display
    decision about WHICH queue shows the email, never a reason to stop counting
    it.
    """
    client, factory = client_and_factory
    email_id = await _seed(factory, subject="fp")
    await _dismiss(client, email_id)

    async with factory() as session:
        total = await EmailRepository().count_email_queue(session)
    assert total == 1
