"""Postgres migration / dialect-compatibility test suite.

These tests run ONLY when a Postgres test URL is configured — env
``TEST_DATABASE_URL`` (preferred) or ``DATABASE_URL`` if it is a ``postgresql``
DSN. On SQLite / no Postgres they skip, so the default secret-free suite is
unaffected. CI provisions a throwaway Postgres service and sets
``TEST_DATABASE_URL`` so they execute there too.

They guard the SQLite→Postgres migration, in particular the ``func.json_extract``
→ dialect-agnostic-accessor fix in the email/audit repositories: on Postgres the
old ``func.json_extract`` raises ``asyncpg.UndefinedFunctionError``, so the two
regression tests below fail loudly if anyone reverts either call site.

Fixture design (deliberate — read before changing):
  * The schema is provisioned ONCE per module, SYNCHRONOUSLY via psycopg2 with
    NO event loop (``pg_schema``). A module-scoped *async* engine would bind to a
    single event loop and then raise asyncpg "another operation is in progress"
    when pytest-asyncio runs the next test on a fresh loop.
  * Each test gets its OWN function-scoped async engine + session (``pg_session``)
    created on that test's loop, and starts from truncated tables.
"""

import os

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.db.models  # noqa: F401  (register tables on Base.metadata)
from app.db.database import Base, _to_async_url
from app.repositories.audit_repository import AuditRepository
from app.repositories.email_repository import EmailRepository


def _raw_pg_url() -> str | None:
    """Return the DEDICATED Postgres test DSN from TEST_DATABASE_URL, if any.

    Deliberately does NOT fall back to the app's DATABASE_URL: this suite runs
    drop_all/create_all, so it must only ever touch a database the operator
    explicitly designated as disposable via TEST_DATABASE_URL — never the app's
    real/dev database.
    """
    url = os.environ.get("TEST_DATABASE_URL", "")
    return url if url.startswith("postgresql") else None


_PG_RAW = _raw_pg_url()

# Whole module skips unless a dedicated Postgres TEST DB is configured — keeps the
# default (SQLite / secret-free) suite green with no Postgres present.
pytestmark = pytest.mark.skipif(
    _PG_RAW is None,
    reason="Postgres not configured; set TEST_DATABASE_URL to a disposable postgresql:// DSN",
)


def _strip_driver(url: str) -> str:
    return url.replace("+asyncpg", "").replace("+psycopg2", "")


def _sync_url(url: str) -> str:
    """psycopg2 (sync) URL — for event-loop-free schema provisioning."""
    return _strip_driver(url).replace("postgresql://", "postgresql+psycopg2://", 1)


def _async_url(url: str) -> str:
    """asyncpg URL — for the per-test app-style async engine."""
    return _strip_driver(url).replace("postgresql://", "postgresql+asyncpg://", 1)


# --- fixtures --------------------------------------------------------------
@pytest.fixture(scope="module")
def pg_schema():
    """Provision the schema once per module, synchronously (psycopg2, no loop).

    drop_all → create_all gives a clean, isolated schema on the target test DB.
    Yields the sync Engine so schema-assertion tests can inspect() it without an
    event loop.
    """
    sync_engine = create_engine(_sync_url(_PG_RAW), future=True)
    Base.metadata.drop_all(sync_engine)
    Base.metadata.create_all(sync_engine)
    yield sync_engine
    Base.metadata.drop_all(sync_engine)
    sync_engine.dispose()


@pytest_asyncio.fixture
async def pg_session(pg_schema):
    """Fresh async engine + session per test (bound to this test's event loop).

    Truncates the app tables first so each test starts empty while the
    module-provisioned schema persists.
    """
    engine = create_async_engine(_async_url(_PG_RAW), future=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as prep:
        await prep.execute(
            text("TRUNCATE emails, audit_logs, chairs RESTART IDENTITY CASCADE")
        )
        await prep.commit()
    async with factory() as session:
        yield session
    await engine.dispose()


def _email_data(lane: str, sender: str = "a@univ.edu") -> dict:
    return {
        "sender": sender,
        "subject": "s",
        "body": "b",
        "classification": {"intent": "submission_deadline", "confidence": 0.9},
        "routing": {"lane": lane},
    }


# --- 1. driver / dialect resolution ----------------------------------------
def test_configured_url_resolves_to_postgres_asyncpg():
    """The app's own URL normalization keeps a Postgres DSN on the asyncpg driver.

    _to_async_url only rewrites SQLite → aiosqlite; a postgresql+asyncpg URL must
    pass through unchanged (that's how the app's async engine talks to Postgres).
    """
    assert (
        _to_async_url("postgresql+asyncpg://u:p@h:5432/db")
        == "postgresql+asyncpg://u:p@h:5432/db"
    )
    assert _to_async_url(_async_url(_PG_RAW)).startswith("postgresql+asyncpg://")


async def test_live_dialect_and_driver_are_postgres_asyncpg(pg_session):
    conn = await pg_session.connection()
    assert conn.dialect.name == "postgresql"
    assert conn.dialect.driver == "asyncpg"
    assert (await pg_session.execute(text("SELECT 1"))).scalar() == 1


# --- 2. schema assertion (post-provision) ----------------------------------
def test_schema_has_all_tables_and_key_columns(pg_schema):
    insp = inspect(pg_schema)
    tables = set(insp.get_table_names())
    assert {"emails", "chairs", "audit_logs", "policy_documents"} <= tables

    # Phase-E columns that must survive the migration on Postgres.
    policy_cols = {c["name"] for c in insp.get_columns("policy_documents")}
    # [tags-dropped E007] tags column dropped by migration e7a9c1f2b3d4; source stays.
    assert {"source"} <= policy_cols
    assert "tags" not in policy_cols
    # audit context column is named "metadata" at the DB level.
    assert "metadata" in {c["name"] for c in insp.get_columns("audit_logs")}
    # the chair FK the queue/analytics aggregates depend on.
    assert "assigned_chair_id" in {c["name"] for c in insp.get_columns("emails")}


# --- 3. CRUD round-trip -----------------------------------------------------
async def test_crud_round_trip(pg_session):
    repo = EmailRepository()
    created = await repo.create_email(pg_session, _email_data("faq"))
    assert created.id is not None

    fetched = await repo.get_email_by_id(pg_session, str(created.id))
    assert fetched is not None
    assert fetched.sender == "a@univ.edu"
    # JSON column round-trips as a dict on Postgres.
    assert fetched.routing["lane"] == "faq"


# --- 4. json_extract fix regression: email_repository lane filter -----------
async def test_lane_filter_uses_dialect_agnostic_json(pg_session):
    """Guards email_repository._queue_conditions.

    Reverting to func.json_extract() would raise UndefinedFunctionError here.
    """
    repo = EmailRepository()
    await repo.create_email(pg_session, _email_data("human_review", "hr@univ.edu"))
    await repo.create_email(pg_session, _email_data("faq", "faq@univ.edu"))

    assert await repo.count_email_queue(pg_session, lane="human_review") == 1
    assert await repo.count_email_queue(pg_session, lane="faq") == 1
    rows = await repo.get_email_queue(pg_session, lane="human_review")
    assert len(rows) == 1
    assert rows[0].routing["lane"] == "human_review"


# --- 5. json_extract fix regression: audit reassignment aggregate ----------
async def test_reassignment_aggregate_uses_dialect_agnostic_json(pg_session):
    """Guards audit_repository.count_reassignments_by_original_chair.

    Groups on extra_metadata["original_chair_id"].as_integer(); the old
    func.json_extract() would raise UndefinedFunctionError on Postgres.
    """
    erepo = EmailRepository()
    arepo = AuditRepository()
    e1 = await erepo.create_email(pg_session, _email_data("human_review", "1@u.edu"))
    e2 = await erepo.create_email(pg_session, _email_data("human_review", "2@u.edu"))

    # Two reassignments away from chair 1, one from "no chair" (None bucket).
    await arepo.create_audit_log(
        pg_session, email_id=str(e1.id), action="chair_reassigned", actor="c",
        details={"original_chair_id": 1, "new_chair_id": 2},
    )
    await arepo.create_audit_log(
        pg_session, email_id=str(e2.id), action="chair_reassigned", actor="c",
        details={"original_chair_id": 1, "new_chair_id": 3},
    )
    await arepo.create_audit_log(
        pg_session, email_id=str(e2.id), action="chair_reassigned", actor="c",
        details={"original_chair_id": None, "new_chair_id": 4},
    )

    dist = await arepo.count_reassignments_by_original_chair(pg_session)
    assert dist.get(1) == 2
    assert dist.get(None) == 1
