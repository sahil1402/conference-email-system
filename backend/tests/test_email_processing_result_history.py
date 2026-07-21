"""Tests for per-message processing-result history (Piece T1b).

T1 stored at most one :class:`EmailProcessingResult` per thread message
(``thread_message_id`` unique). T1b drops that uniqueness so a follow-up can be
reprocessed and keep its full history — multiple result rows per message,
returned oldest-first via the ``EmailThreadMessage.processing_results``
relationship, and all removed together when the parent message is deleted.

Hermetic: ORM tests use a throwaway in-memory SQLite built with
``Base.metadata.create_all`` (FK PRAGMA on so CASCADE fires). The migration
round-trip test runs Alembic against a temp SQLite *file* in a subprocess with
``DATABASE_URL`` overridden — never the real/demo database.
"""

import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import Email, EmailProcessingResult, EmailThreadMessage
from app.models.enums import EmailSource, EmailStatus, MessageAuthorRole

BACKEND_ROOT = Path(__file__).resolve().parents[1]


# --- ORM-level tests (in-memory SQLite) ------------------------------------


@pytest.fixture
def session() -> Session:
    """Synchronous in-memory SQLite session with FK enforcement on."""
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # pragma: no cover - trivial
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    Base.metadata.drop_all(engine)


def _message(session) -> EmailThreadMessage:
    """Create + persist a parent Email and one thread message; return the msg."""
    email = Email(
        sender="author@university.edu",
        subject="Question about the deadline",
        body="When is the submission deadline?",
        status=EmailStatus.PENDING.value,
        source=EmailSource.ZENDESK.value,
        zendesk_ticket_id=22010,
    )
    session.add(email)
    session.flush()
    msg = EmailThreadMessage(
        public=True,
        author_role=MessageAuthorRole.END_USER.value,
        created_at=datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc),
        email_id=email.id,
        plain_body="a follow-up question",
    )
    session.add(msg)
    session.flush()
    return msg


def _result(msg_id: int, created_at: datetime, **overrides) -> EmailProcessingResult:
    base = dict(thread_message_id=msg_id, created_at=created_at)
    base.update(overrides)
    return EmailProcessingResult(**base)


def test_multiple_results_per_message_allowed(session):
    """T1b: the same thread_message_id may have several result rows.

    Under T1's unique index the second insert raised IntegrityError; now it
    commits cleanly.
    """
    msg = _message(session)
    session.add_all([
        _result(msg.id, datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
                lane="HUMAN_REVIEW", confidence=0.61),
        _result(msg.id, datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc),
                lane="AUTO_REPLY", confidence=0.88),
    ])
    session.commit()  # must NOT raise

    count = session.scalar(
        select(EmailProcessingResult)
        .where(EmailProcessingResult.thread_message_id == msg.id)
    )
    all_rows = session.scalars(
        select(EmailProcessingResult)
        .where(EmailProcessingResult.thread_message_id == msg.id)
    ).all()
    assert count is not None
    assert len(all_rows) == 2


def test_processing_results_relationship_ordered_oldest_first(session):
    """The relationship yields results chronologically (oldest-first)."""
    msg = _message(session)
    # Insert out of chronological order to prove the relationship sorts.
    session.add_all([
        _result(msg.id, datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc), lane="latest"),
        _result(msg.id, datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc), lane="earliest"),
        _result(msg.id, datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc), lane="middle"),
    ])
    session.commit()
    session.refresh(msg)

    # SQLite round-trips datetimes tz-naive, so compare the sequence's own sort
    # rather than exact tz-aware values.
    times = [r.created_at for r in msg.processing_results]
    assert times == sorted(times), "processing_results must yield oldest-first"
    assert [r.lane for r in msg.processing_results] == ["earliest", "middle", "latest"]


def test_processing_results_tiebreak_on_id_when_created_at_equal(session):
    """Equal created_at values fall back to id order (insertion order)."""
    msg = _message(session)
    same = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    first = _result(msg.id, same, lane="HUMAN_REVIEW")
    session.add(first)
    session.flush()
    second = _result(msg.id, same, lane="AUTO_REPLY")
    session.add(second)
    session.commit()
    session.refresh(msg)

    ids = [r.id for r in msg.processing_results]
    assert ids == sorted(ids), "ties on created_at resolve by ascending id"
    assert msg.processing_results[0].id == first.id


def test_cascade_delete_removes_all_results(session):
    """Deleting the parent message removes ALL its results, not just one."""
    msg = _message(session)
    session.add_all([
        _result(msg.id, datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)),
        _result(msg.id, datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)),
        _result(msg.id, datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)),
    ])
    session.commit()
    assert len(session.scalars(select(EmailProcessingResult)).all()) == 3

    session.delete(msg)
    session.commit()

    assert session.scalar(select(EmailProcessingResult)) is None
    # Sanity: the parent message really is gone too.
    assert session.scalar(select(EmailThreadMessage)) is None


def test_cascade_delete_via_email_removes_results(session):
    """Deleting the grandparent Email also removes results (transitive CASCADE)."""
    msg = _message(session)
    session.add(_result(msg.id, datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)))
    session.commit()

    email = session.scalar(select(Email))
    session.delete(email)
    session.commit()

    assert session.scalar(select(EmailProcessingResult)) is None
    assert session.scalar(select(EmailThreadMessage)) is None


# --- Migration round-trip test (subprocess, temp SQLite file) --------------


def _run_alembic(args, db_url: str):
    env = {**os.environ, "DATABASE_URL": db_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


_T1_REVISION = "d4e5f6a7b8c9"  # T1 (unique index) — the T1b down_revision.
_INDEX = "ix_email_processing_results_thread_message_id"


def _index_is_unique(db_file) -> bool:
    con = sqlite3.connect(db_file)
    try:
        for _seq, name, unique, *_ in con.execute(
            "PRAGMA index_list(email_processing_results)"
        ):
            if name == _INDEX:
                return bool(unique)
        raise AssertionError(f"{_INDEX} not found")
    finally:
        con.close()


def test_migration_drops_and_restores_uniqueness(tmp_path):
    """head → index non-unique; downgrade to T1 → unique again; re-upgrade clean.

    Runs against a throwaway SQLite file — never the real/demo database.
    """
    db_file = tmp_path / "t1b_roundtrip.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    up = _run_alembic(["upgrade", "head"], db_url)
    assert up.returncode == 0, f"upgrade failed:\n{up.stderr}"
    assert _index_is_unique(db_file) is False, "T1b must make the FK index non-unique"

    down = _run_alembic(["downgrade", _T1_REVISION], db_url)
    assert down.returncode == 0, f"downgrade failed:\n{down.stderr}"
    assert _index_is_unique(db_file) is True, "downgrade must restore the unique index"

    up2 = _run_alembic(["upgrade", "head"], db_url)
    assert up2.returncode == 0, f"re-upgrade failed:\n{up2.stderr}"
    assert _index_is_unique(db_file) is False
