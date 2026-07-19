"""Tests for the Zendesk ticket schema (Piece 3): models + migration.

Hermetic: the ORM tests run against a throwaway in-memory SQLite database built
with ``Base.metadata.create_all`` (no real DB, no network). The migration
round-trip test runs Alembic against a temp SQLite *file* in a subprocess with
``DATABASE_URL`` overridden — it never touches the real/demo Postgres database.
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
from app.db.models import Email, EmailThreadMessage
from app.models.enums import EmailSource, EmailStatus, MessageAuthorRole

BACKEND_ROOT = Path(__file__).resolve().parents[1]


# --- ORM-level tests (in-memory SQLite) ------------------------------------


@pytest.fixture
def session() -> Session:
    """A synchronous in-memory SQLite session with FK enforcement on.

    A sync session keeps these pure-schema tests simple; SQLite needs the
    foreign_keys PRAGMA enabled for ON DELETE CASCADE to actually fire.
    """
    engine = create_engine("sqlite://", future=True)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # pragma: no cover - trivial
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    Base.metadata.drop_all(engine)


def _make_email(**overrides) -> Email:
    base = dict(
        sender="author@university.edu",
        subject="Question about the deadline",
        body="When is the submission deadline?",
        status=EmailStatus.PENDING.value,
    )
    base.update(overrides)
    return Email(**base)


def _msg(created_at: datetime, *, public: bool, role: str, **overrides) -> EmailThreadMessage:
    base = dict(public=public, author_role=role, created_at=created_at)
    base.update(overrides)
    return EmailThreadMessage(**base)


def test_email_source_defaults_to_toy_dataset(session):
    """A plain (non-Zendesk) email keeps working, defaulting source."""
    email = _make_email()
    session.add(email)
    session.commit()
    session.refresh(email)
    assert email.source == EmailSource.TOY_DATASET.value
    assert email.zendesk_ticket_id is None
    assert email.thread_messages == []


def test_email_to_many_thread_messages_ordered(session):
    """Email -> many EmailThreadMessages, returned oldest-first."""
    email = _make_email(source=EmailSource.ZENDESK.value, zendesk_ticket_id=22009)
    session.add(email)
    session.flush()

    later = _msg(datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc),
                 public=True, role=MessageAuthorRole.AGENT.value, email_id=email.id)
    earlier = _msg(datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc),
                   public=True, role=MessageAuthorRole.END_USER.value, email_id=email.id)
    session.add_all([later, earlier])
    session.commit()

    session.refresh(email)
    times = [m.created_at for m in email.thread_messages]
    assert times == sorted(times), "relationship must yield oldest-first"
    assert len(email.thread_messages) == 2


def test_cascade_delete_removes_thread_messages(session):
    email = _make_email(source=EmailSource.ZENDESK.value, zendesk_ticket_id=101)
    session.add(email)
    session.flush()
    session.add(
        _msg(datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc),
             public=True, role=MessageAuthorRole.END_USER.value, email_id=email.id)
    )
    session.commit()

    session.delete(email)
    session.commit()
    assert session.scalar(select(EmailThreadMessage)) is None


def test_zendesk_ticket_id_unique(session):
    session.add(_make_email(source=EmailSource.ZENDESK.value, zendesk_ticket_id=555))
    session.commit()
    session.add(_make_email(source=EmailSource.ZENDESK.value, zendesk_ticket_id=555))
    with pytest.raises(IntegrityError):
        session.commit()


def test_multiple_null_ticket_ids_allowed(session):
    """Non-Zendesk rows (NULL ticket id) must coexist despite the unique index."""
    session.add_all([_make_email(), _make_email()])
    session.commit()  # must not raise
    assert session.scalars(select(Email)).all().__len__() == 2


def test_zendesk_comment_id_unique(session):
    email = _make_email(source=EmailSource.ZENDESK.value, zendesk_ticket_id=777)
    session.add(email)
    session.flush()
    ts = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)
    session.add(_msg(ts, public=True, role="end-user", email_id=email.id, zendesk_comment_id=9001))
    session.commit()
    session.add(_msg(ts, public=False, role="agent", email_id=email.id, zendesk_comment_id=9001))
    with pytest.raises(IntegrityError):
        session.commit()


def test_crud_thread_message_fields(session):
    """Basic CRUD exercising the load-bearing columns."""
    email = _make_email(source=EmailSource.ZENDESK.value, zendesk_ticket_id=42)
    session.add(email)
    session.flush()
    msg = _msg(
        datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc),
        public=False,
        role=MessageAuthorRole.AGENT.value,
        email_id=email.id,
        zendesk_comment_id=12345,
        author_id=20978392,
        plain_body="internal note text",
        html_body="<p>internal note text</p>",
        via_channel="email",
    )
    session.add(msg)
    session.commit()

    loaded = session.scalar(select(EmailThreadMessage).where(EmailThreadMessage.zendesk_comment_id == 12345))
    assert loaded.public is False
    assert loaded.author_role == "agent"
    assert loaded.plain_body == "internal note text"
    assert loaded.ingested_at is not None  # server_default applied


def test_initial_inquiry_is_first_public_end_user_message(session):
    """The classified message = first public end-user comment, oldest-first.

    A private end-user note and an earlier agent auto-ack must NOT be picked.
    """
    email = _make_email(source=EmailSource.ZENDESK.value, zendesk_ticket_id=900)
    session.add(email)
    session.flush()

    def at(hour):
        return datetime(2026, 7, 15, hour, 0, tzinfo=timezone.utc)

    session.add_all([
        # earliest, but an agent auto-reply — not the inquiry
        _msg(at(8), public=True, role="agent", email_id=email.id, plain_body="auto-ack"),
        # a private end-user note — not public, skip
        _msg(at(9), public=False, role="end-user", email_id=email.id, plain_body="private aside"),
        # THE initial inquiry: first public end-user message
        _msg(at(10), public=True, role="end-user", email_id=email.id, plain_body="the real question"),
        # a later public end-user follow-up — must not win
        _msg(at(11), public=True, role="end-user", email_id=email.id, plain_body="follow-up"),
    ])
    session.commit()

    initial = session.scalars(
        select(EmailThreadMessage)
        .where(
            EmailThreadMessage.email_id == email.id,
            EmailThreadMessage.public.is_(True),
            EmailThreadMessage.author_role == MessageAuthorRole.END_USER.value,
        )
        .order_by(EmailThreadMessage.created_at.asc())
    ).first()

    assert initial.plain_body == "the real question"


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


def test_migration_upgrade_and_downgrade_roundtrip(tmp_path):
    """`alembic upgrade head` creates the schema; `downgrade -1` reverses it.

    Runs against a throwaway SQLite file — never the real/demo database.
    """
    db_file = tmp_path / "roundtrip.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    up = _run_alembic(["upgrade", "head"], db_url)
    assert up.returncode == 0, f"upgrade failed:\n{up.stderr}"

    con = sqlite3.connect(db_file)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "email_thread_messages" in tables
        email_cols = {r[1] for r in con.execute("PRAGMA table_info(emails)")}
        assert {"source", "zendesk_ticket_id", "last_processed_comment_id"} <= email_cols
    finally:
        con.close()

    # Reverse only our migration (down one revision).
    down = _run_alembic(["downgrade", "-1"], db_url)
    assert down.returncode == 0, f"downgrade failed:\n{down.stderr}"

    con = sqlite3.connect(db_file)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "email_thread_messages" not in tables
        email_cols = {r[1] for r in con.execute("PRAGMA table_info(emails)")}
        assert "zendesk_ticket_id" not in email_cols
        assert "source" not in email_cols
    finally:
        con.close()

    # And re-upgrading is clean (proves the down/up cycle is repeatable).
    up2 = _run_alembic(["upgrade", "head"], db_url)
    assert up2.returncode == 0, f"re-upgrade failed:\n{up2.stderr}"
