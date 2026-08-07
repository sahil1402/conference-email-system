"""The pipeline's extraction is persisted on Email and EmailProcessingResult.

Covers the subtask-4 DB layer: the migration round-trips, a processed email's
``extraction`` column survives a write + read, per-follow-up rows carry their
own, and NULL (every row predating the column) breaks nothing on read.

SCOPE LIMIT: persistence only. No endpoint serves ``extraction`` yet, so nothing
here asserts on an API response.
"""

import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.models import Base, Email, EmailProcessingResult, EmailThreadMessage
from app.pipeline.distiller import DistillResult
from app.pipeline.orchestrator import EmailPipeline
from app.repositories.email_repository import EmailRepository

BACKEND_ROOT = Path(__file__).resolve().parents[1]


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


class _StubRetriever:
    async def retrieve(self, query, intent, top_k=3, *, prior_intent=""):
        return []


class _StubDistiller:
    def __init__(self, result):
        self.result = result

    async def distill(self, subject, body, *, transcript=None):
        return self.result


def _pipeline():
    pipeline = EmailPipeline()
    pipeline.retriever = _StubRetriever()
    return pipeline


_EMAIL = {
    "from": "jane@example.edu",
    "sender_name": "Jane Roe",
    "subject": "Re: Desk Rejection of Your Submission 22336",
    "body": "Dear chairs, we would like to appeal.",
}


# ---------------------------------------------------------------------------
# Migration round-trip (subprocess, temp SQLite file)
# ---------------------------------------------------------------------------
_PREV_REVISION = "f8b2c4d6e0a1"  # suggestion_audit_logs — this migration's down_revision.


def _run_alembic(args, db_url: str):
    env = {**os.environ, "DATABASE_URL": db_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def _columns(db_file, table: str) -> list[str]:
    con = sqlite3.connect(db_file)
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    finally:
        con.close()


def _indexes(db_file, table: str) -> list[str]:
    con = sqlite3.connect(db_file)
    try:
        return sorted(
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
                (table,),
            )
        )
    finally:
        con.close()


def test_extraction_migration_round_trips(tmp_path):
    """upgrade → downgrade → upgrade, on BOTH tables.

    The downgrade is genuinely exercised rather than assumed from the column
    type: dropping a column under SQLite goes through ``batch_alter_table``,
    which RECREATES the table, so the reversal is only safe if the rebuilt
    table keeps its rows and indexes. Both are asserted below.
    """
    db_file = tmp_path / "extraction_roundtrip.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    up = _run_alembic(["upgrade", "head"], db_url)
    assert up.returncode == 0, f"upgrade failed:\n{up.stderr}"
    for table in ("emails", "email_processing_results"):
        assert "extraction" in _columns(db_file, table), table

    before = {t: _indexes(db_file, t) for t in ("emails", "email_processing_results")}
    con = sqlite3.connect(db_file)
    con.execute(
        "INSERT INTO emails (sender, subject, body, status, extraction) "
        "VALUES ('a@x.edu', 's', 'b', 'draft_generated', '{\"method\":\"none\"}')"
    )
    con.commit()
    con.close()

    down = _run_alembic(["downgrade", _PREV_REVISION], db_url)
    assert down.returncode == 0, f"downgrade failed:\n{down.stderr}"
    for table in ("emails", "email_processing_results"):
        assert "extraction" not in _columns(db_file, table), table
        # The batch recreate must not silently drop indexes or rows.
        assert _indexes(db_file, table) == before[table], table
    con = sqlite3.connect(db_file)
    assert con.execute("SELECT count(*) FROM emails").fetchone()[0] == 1
    con.close()

    up2 = _run_alembic(["upgrade", "head"], db_url)
    assert up2.returncode == 0, f"re-upgrade failed:\n{up2.stderr}"
    for table in ("emails", "email_processing_results"):
        assert "extraction" in _columns(db_file, table), table
        assert _indexes(db_file, table) == before[table], table


# ---------------------------------------------------------------------------
# Email.extraction round-trips through a write + read
# ---------------------------------------------------------------------------
async def test_processed_email_persists_extraction(session):
    result = await _pipeline().process_email(_EMAIL, session)

    email = await EmailRepository().get_email_by_id(session, result.email_id)
    assert email.extraction is not None
    assert email.extraction["submission_number"] == "22336"
    assert email.extraction["openreview_forum_id"] is None
    assert email.extraction["method"] == "regex_fallback"
    assert email.extraction["authors"] == [
        {"name": "Jane Roe", "email": "jane@example.edu", "affiliation": None}
    ]


async def test_extraction_round_trips_every_field_from_the_llm_path(
    session, monkeypatch
):
    """All four fields survive the JSON write + read, populated."""
    monkeypatch.setattr(settings, "QUERY_STRATEGY", "distill")
    pipeline = _pipeline()
    pipeline.distiller = _StubDistiller(
        DistillResult(
            queries=["q"],
            intent="desk_reject_appeal",
            confidence=0.9,
            submission_number_raw="99999",
            openreview_id_raw="Ab3xY9kLm2",
            authors_raw=[
                "Jane Roe | jane@example.edu | Example University",
                "John Doe | NONE | NONE",
            ],
        )
    )
    result = await pipeline.process_email(_EMAIL, session)

    stored = (await EmailRepository().get_email_by_id(session, result.email_id)).extraction
    assert stored == {
        "submission_number": "99999",
        "openreview_forum_id": "Ab3xY9kLm2",
        "authors": [
            {
                "name": "Jane Roe",
                "email": "jane@example.edu",
                "affiliation": "Example University",
            },
            {"name": "John Doe", "email": None, "affiliation": None},
        ],
        "method": "llm_distiller",
    }


async def test_extraction_is_serialized_with_model_dump(session):
    """Same pattern as classification/routing/draft — a plain dict, not a model."""
    result = await _pipeline().process_email(_EMAIL, session)
    email = await EmailRepository().get_email_by_id(session, result.email_id)
    assert isinstance(email.extraction, dict)
    assert set(email.extraction) == {
        "submission_number",
        "openreview_forum_id",
        "authors",
        "method",
    }


async def test_reprocess_updates_the_stored_extraction(session):
    """The reprocess path's allowlist must include extraction, or a redraft
    would leave a stale extraction beside a fresh draft."""
    pipeline = _pipeline()
    created = await pipeline.process_email(_EMAIL, session)
    email = await EmailRepository().get_email_by_id(session, created.email_id)
    email.extraction = {"submission_number": "00000", "method": "none",
                        "openreview_forum_id": None, "authors": []}
    await session.commit()

    await pipeline.reprocess_email(session, email)

    refreshed = await EmailRepository().get_email_by_id(session, created.email_id)
    assert refreshed.extraction["submission_number"] == "22336"
    assert refreshed.extraction["method"] == "regex_fallback"


# ---------------------------------------------------------------------------
# EmailProcessingResult carries its own extraction
# ---------------------------------------------------------------------------
async def test_processing_result_persists_extraction(session):
    """A follow-up message's own result row stores its own extraction."""
    pipeline = _pipeline()
    created = await pipeline.process_email(_EMAIL, session)

    message = EmailThreadMessage(
        email_id=int(created.email_id),
        public=True,
        author_role="end-user",
        plain_body="Following up about submission 44444.",
        created_at=datetime.now(timezone.utc),
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)

    computed = await pipeline.compute(
        {
            "from": "jane@example.edu",
            "sender_name": "Jane Roe",
            "subject": "Follow-up",
            "body": "Following up about submission 44444.",
        },
        session,
    )
    row = await EmailRepository().add_processing_result(
        session, message.id, computed.record
    )

    assert row.extraction is not None
    assert row.extraction["submission_number"] == "44444"
    assert row.extraction["method"] == "regex_fallback"


async def test_processing_result_extraction_is_independent_of_the_parent(session):
    """A follow-up can name a different submission than the opening inquiry."""
    pipeline = _pipeline()
    created = await pipeline.process_email(_EMAIL, session)
    parent = await EmailRepository().get_email_by_id(session, created.email_id)

    message = EmailThreadMessage(
        email_id=int(created.email_id),
        public=True,
        author_role="end-user",
        plain_body="Also about submission 44444.",
        created_at=datetime.now(timezone.utc),
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)

    computed = await pipeline.compute(
        {"from": "jane@example.edu", "subject": "F", "body": "About submission 44444."},
        session,
    )
    row = await EmailRepository().add_processing_result(
        session, message.id, computed.record
    )

    assert parent.extraction["submission_number"] == "22336"
    assert row.extraction["submission_number"] == "44444"


# ---------------------------------------------------------------------------
# NULL extraction (every row predating the column) must break nothing
# ---------------------------------------------------------------------------
async def test_legacy_email_with_null_extraction_reads_fine(session):
    """A pre-migration row has extraction NULL; reads must not assume presence."""
    email = Email(
        sender="legacy@example.edu",
        subject="Legacy row",
        body="Predates the extraction column.",
        status="draft_generated",
    )
    session.add(email)
    await session.commit()
    await session.refresh(email)

    fetched = await EmailRepository().get_email_by_id(session, str(email.id))
    assert fetched is not None
    assert fetched.extraction is None
    # The queue read path must tolerate it too (no KeyError / no filtering out).
    queue = await EmailRepository().get_email_queue(session)
    assert any(e.id == email.id for e in queue)


async def test_legacy_processing_result_with_null_extraction_reads_fine(session):
    email = Email(
        sender="legacy@example.edu", subject="s", body="b", status="draft_generated"
    )
    session.add(email)
    await session.commit()
    await session.refresh(email)

    message = EmailThreadMessage(
        email_id=email.id,
        public=True,
        created_at=datetime.now(timezone.utc),
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)

    row = EmailProcessingResult(thread_message_id=message.id)
    session.add(row)
    await session.commit()

    found = (
        await session.execute(
            select(EmailProcessingResult).where(
                EmailProcessingResult.thread_message_id == message.id
            )
        )
    ).scalar_one()
    assert found.extraction is None


async def test_add_processing_result_tolerates_a_record_without_extraction(session):
    """A record built before this column existed must still persist."""
    email = Email(
        sender="a@example.edu", subject="s", body="b", status="draft_generated"
    )
    session.add(email)
    await session.commit()
    await session.refresh(email)

    message = EmailThreadMessage(
        email_id=email.id,
        public=True,
        created_at=datetime.now(timezone.utc),
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)

    row = await EmailRepository().add_processing_result(
        session, message.id, {"classification": {}, "routing": {}, "draft": {}}
    )
    assert row.extraction is None
