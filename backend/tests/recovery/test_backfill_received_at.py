"""Tests for scripts/recovery/backfill_received_at.py.

House rule 5 (`scripts/recovery/README.md`): even a one-off gets a test, because
these scripts run against production data exactly once, under time pressure, with
no staging rehearsal.

Everything here runs against a throwaway SQLite file built with the real ``Email``
model — never a real or demo database. The fixture reproduces the confirmed
production SHAPE (rows needing backfill, long-drift outliers, and rows with a NULL
``zendesk_created_at`` that must be skipped) at small scale.

SCOPE LIMIT — SQLite is not PostgreSQL in two ways that matter here:
  * ``is_distinct_from`` renders ``IS NOT`` rather than ``IS DISTINCT FROM``.
    Both have the same NULL-safe semantics, which is the property under test.
  * The dry-run ``SET TRANSACTION READ ONLY`` belt-and-braces is PostgreSQL-only
    and is skipped by the script on SQLite, so these tests prove dry-run purity
    by observing that no row and no file changed, NOT by relying on that guard.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Email
from scripts.recovery import backfill_received_at as bru

# A fixed "import moment" and ticket-open times, so drift is deterministic.
IMPORT_AT = datetime(2026, 7, 21, 7, 54, tzinfo=timezone.utc)
UPDATED_AT_SENTINEL = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def _naive(value: datetime) -> datetime:
    """SQLite stores DATETIME without tzinfo; compare like-for-like."""
    return value.replace(tzinfo=None) if value.tzinfo else value


@pytest_asyncio.fixture
async def db(tmp_path):
    """A throwaway SQLite file shaped like production, plus its session factory."""
    path = tmp_path / "backfill.db"
    url = f"sqlite+aiosqlite:///{path.as_posix()}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    rows = []
    # 3 live-polled rows: drift of minutes.
    for i in range(3):
        opened = IMPORT_AT + timedelta(days=2, minutes=i)
        rows.append((opened + timedelta(minutes=4), opened, "zendesk"))
    # 2 bulk-imported historical rows: drift of months.
    for i in range(2):
        rows.append((IMPORT_AT + timedelta(seconds=i), IMPORT_AT - timedelta(days=120 + i), "zendesk"))
    # 1 long-drift outlier: the >=400d class that is re-printed every run.
    rows.append((IMPORT_AT, IMPORT_AT - timedelta(days=900), "zendesk"))
    # 2 rows with NULL zendesk_created_at -> must never be touched.
    for i in range(2):
        rows.append((IMPORT_AT + timedelta(hours=i), None, "toy_dataset"))

    async with factory() as session:
        for n, (received, created, source) in enumerate(rows):
            session.add(
                Email(
                    sender=f"a{n}@u.edu",
                    subject="s",
                    body="b",
                    status="DRAFT_GENERATED",
                    zendesk_status="open" if created else None,
                    source=source,
                    zendesk_ticket_id=20000 + n if created else None,
                    received_at=received,
                    zendesk_created_at=created,
                    updated_at=UPDATED_AT_SENTINEL,
                )
            )
        await session.commit()

    yield url, factory
    await engine.dispose()


async def _all_rows(factory):
    """Every column a backfill must not disturb, ordered for stable comparison."""
    async with factory() as session:
        result = await session.execute(
            select(
                Email.id,
                Email.received_at,
                Email.zendesk_created_at,
                Email.updated_at,
                Email.status,
                Email.zendesk_status,
                Email.source,
            ).order_by(Email.id)
        )
        return result.all()


@pytest.fixture(autouse=True)
def isolated_output(tmp_path, monkeypatch):
    """Redirect rollback files to a temp dir so tests never touch scripts/recovery/output."""
    out = tmp_path / "output"
    monkeypatch.setattr(bru, "OUTPUT_DIR", out)
    return out


# --- 1. column guard -------------------------------------------------------


def test_guard_accepts_the_real_statement():
    """The statement the script actually issues passes the guard."""
    statement = (
        update(Email)
        .where(bru._affected_predicate())
        .values(received_at=Email.zendesk_created_at, updated_at=Email.updated_at)
    )
    bru._guard_update_columns(str(statement.compile()))  # must not raise


def test_guard_rejects_updated_at_onupdate_regression():
    """REGRESSION: SQLAlchemy's onupdate silently restamps updated_at.

    ``Email.updated_at`` is declared ``onupdate=func.now()``, so omitting an
    explicit value makes SQLAlchemy append ``updated_at=CURRENT_TIMESTAMP`` to the
    SET clause — rewriting the real "last modified" time on every backfilled row.
    That column is load-bearing evidence (a prior incident used an unmoved
    updated_at to prove a sweep had NOT written to a row), so this must be caught,
    not shipped. This is the exact statement the script had before the fix.
    """
    statement = (
        update(Email)
        .where(bru._affected_predicate())
        .values(received_at=Email.zendesk_created_at)  # no updated_at pin
    )
    # Checked on BOTH dialects: the onupdate renders as now() by default and as
    # CURRENT_TIMESTAMP on SQLite, so asserting either literal would be a
    # dialect-specific test of the wrong property. What matters is that the SET
    # clause assigns updated_at to something OTHER than its own value.
    for dialect in (None, sqlite_dialect()):
        compiled = str(
            statement.compile(dialect=dialect) if dialect else statement.compile()
        )
        # Precondition: prove SQLAlchemy really does inject it, so this test can
        # never pass vacuously if that behaviour changes.
        assert "updated_at" in compiled, compiled
        assert not bru.UPDATED_AT_SELF_ASSIGNMENT.search(compiled), compiled

        with pytest.raises(bru.ColumnGuardError, match="updated_at"):
            bru._guard_update_columns(compiled)


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("UPDATE emails SET received_at=emails.zendesk_created_at, status='x'", "status"),
        ("UPDATE emails SET received_at=emails.zendesk_created_at, zendesk_status='new'", "zendesk_status"),
        ("UPDATE emails SET received_at=emails.zendesk_created_at, body='x'", "body"),
        ("UPDATE emails SET received_at=emails.zendesk_created_at, source='zendesk'", "source"),
        ("UPDATE emails SET received_at=emails.zendesk_created_at, redrafting=1", "redrafting"),
    ],
)
def test_guard_rejects_other_columns(sql, expected):
    """Any column outside the allowed set aborts the run, named in the error."""
    with pytest.raises(bru.ColumnGuardError, match=expected):
        bru._guard_update_columns(sql)


def test_guard_does_not_false_positive_on_created_at_substring():
    """``created_at`` is a forbidden column AND a substring of the allowed one.

    A naive substring check would reject the legitimate statement; the guard
    matches on word boundaries instead.
    """
    ok = "UPDATE emails SET received_at=emails.zendesk_created_at WHERE emails.zendesk_created_at IS NOT NULL"
    bru._guard_update_columns(ok)  # must not raise

    with pytest.raises(bru.ColumnGuardError, match="created_at"):
        bru._guard_update_columns(ok + ", created_at=CURRENT_TIMESTAMP")


def test_guard_rejects_updated_at_assigned_to_anything_but_itself():
    """updated_at is allowed ONLY pinned to its own value."""
    bru._guard_update_columns(
        "UPDATE emails SET received_at=emails.zendesk_created_at, updated_at=emails.updated_at"
    )
    for bad in (
        "UPDATE emails SET received_at=emails.zendesk_created_at, updated_at=CURRENT_TIMESTAMP",
        "UPDATE emails SET received_at=emails.zendesk_created_at, updated_at=emails.received_at",
        "UPDATE emails SET received_at=emails.zendesk_created_at, updated_at='2026-01-01'",
    ):
        with pytest.raises(bru.ColumnGuardError):
            bru._guard_update_columns(bad)


# --- 2. NULL zendesk_created_at is always skipped -------------------------


async def test_null_zendesk_created_at_rows_are_never_selected(db):
    """The snapshot excludes NULL-source rows, so they can never be updated."""
    url, factory = db
    exit_code = await bru._run(execute=False, assume_yes=False, database_url=url)
    assert exit_code == 0

    before = await _all_rows(factory)
    null_rows = [r for r in before if r.zendesk_created_at is None]
    assert len(null_rows) == 2, "fixture must contain NULL-zendesk_created_at rows"

    await bru._run(execute=True, assume_yes=True, database_url=url)

    after = {r.id: r for r in await _all_rows(factory)}
    for row in null_rows:
        assert after[row.id].received_at == row.received_at
        assert after[row.id].zendesk_created_at is None
    # And the column is never nulled anywhere.
    assert all(r.received_at is not None for r in after.values())


async def test_execute_never_touches_status_or_other_columns(db):
    """Only received_at moves; status/zendesk_status/source/updated_at hold."""
    url, factory = db
    before = {r.id: r for r in await _all_rows(factory)}

    await bru._run(execute=True, assume_yes=True, database_url=url)

    after = {r.id: r for r in await _all_rows(factory)}
    for row_id, old in before.items():
        new = after[row_id]
        assert new.status == old.status
        assert new.zendesk_status == old.zendesk_status
        assert new.source == old.source
        # updated_at must survive SQLAlchemy's onupdate.
        assert new.updated_at == old.updated_at == _naive(UPDATED_AT_SENTINEL)
        if old.zendesk_created_at is not None:
            assert new.received_at == old.zendesk_created_at


# --- 3. dry run performs zero writes and writes no rollback file ----------


async def test_dry_run_changes_nothing_and_writes_no_file(db, isolated_output):
    """Default mode is inert: same rows, same values, no rollback artifact."""
    url, factory = db
    before = await _all_rows(factory)

    exit_code = await bru._run(execute=False, assume_yes=False, database_url=url)

    assert exit_code == 0
    assert await _all_rows(factory) == before
    assert not isolated_output.exists() or list(isolated_output.glob("*.json")) == []


async def test_dry_run_reports_the_rows_it_would_change(db, capsys):
    """The dry run names the count and prints the long-drift outlier by id."""
    url, factory = db
    await bru._run(execute=False, assume_yes=False, database_url=url)
    out = capsys.readouterr().out

    # 6 changeable rows in the fixture (3 live-polled + 2 historical + 1 outlier);
    # the 2 NULL rows are excluded.
    assert "rows that would change: 6" in out
    assert "DRY RUN" in out
    assert "no UPDATE issued" in out
    # The >=400d outlier is surfaced for eyeball re-confirmation.
    assert "known long-drift outliers" in out
    assert "900.0d" in out


async def test_dry_run_warns_when_outlier_count_differs_from_verified(db, capsys):
    """The fixture has 1 outlier but 4 were hand-verified -> loud NOTE.

    Guards the "a new outlier rode along unnoticed" failure mode.
    """
    url, _ = db
    await bru._run(execute=False, assume_yes=False, database_url=url)
    out = capsys.readouterr().out
    assert f"but {bru.EXPECTED_OUTLIERS} " in out
    assert "Re-verify the new ones" in out


# --- 4. rollback file is written BEFORE the UPDATE ------------------------


async def test_rollback_file_exists_before_update_and_data_is_intact_on_failure(
    db, isolated_output, monkeypatch
):
    """A crash between the rollback file and the commit leaves NO partial state.

    The ordering under test is: snapshot -> write+fsync rollback file -> UPDATE ->
    verify -> commit. Failing the UPDATE proves the file is already durable at
    that point AND that the data is untouched, so an operator always has a
    rollback record for any change that could have been committed.
    """
    url, factory = db
    before = await _all_rows(factory)
    observed: dict = {}

    # Crash at the guard, i.e. after the rollback file is written but before the
    # UPDATE is executed — the narrowest window that proves the ordering.
    original_guard = bru._guard_update_columns

    def guard_then_crash(sql):
        original_guard(sql)
        observed["files_at_update_time"] = list(isolated_output.glob("*.json"))
        raise RuntimeError("simulated crash immediately after the rollback file")

    monkeypatch.setattr(bru, "_guard_update_columns", guard_then_crash)

    with pytest.raises(RuntimeError, match="simulated crash"):
        await bru._run(execute=True, assume_yes=True, database_url=url)

    # (a) the rollback file already existed at the moment the UPDATE was reached
    assert len(observed["files_at_update_time"]) == 1
    # (b) the data was NOT changed
    assert await _all_rows(factory) == before


async def test_rollback_file_contents_allow_a_real_restore(db, isolated_output):
    """The file records every affected id with its PRIOR received_at."""
    url, factory = db
    before = {r.id: r for r in await _all_rows(factory)}

    await bru._run(execute=True, assume_yes=True, database_url=url)

    files = list(isolated_output.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["row_count"] == 6
    assert len(payload["rows"]) == 6
    for entry in payload["rows"]:
        old = before[entry["id"]]
        assert entry["prior_received_at"] == old.received_at.isoformat()
        assert entry["new_received_at"] == old.zendesk_created_at.isoformat()
    # No NULL-zendesk_created_at row may appear in the rollback set.
    null_ids = {r.id for r in before.values() if r.zendesk_created_at is None}
    assert null_ids.isdisjoint({e["id"] for e in payload["rows"]})


async def test_verification_failure_rolls_back_and_leaves_data_unchanged(
    db, isolated_output, monkeypatch
):
    """A post-UPDATE mismatch aborts instead of committing a half-checked change."""
    url, factory = db
    before = await _all_rows(factory)

    # Force the rowcount check to disagree with the snapshot.
    original_snapshot = bru._snapshot
    calls = {"n": 0}

    async def snapshot_then_lie(conn):
        calls["n"] += 1
        rows = await original_snapshot(conn)
        if calls["n"] == 1:
            return rows + [
                bru.AffectedRow(
                    email_id=-1,
                    received_at=IMPORT_AT,
                    zendesk_created_at=IMPORT_AT - timedelta(days=1),
                )
            ]
        return rows

    monkeypatch.setattr(bru, "_snapshot", snapshot_then_lie)

    exit_code = await bru._run(execute=True, assume_yes=True, database_url=url)

    assert exit_code == 4
    assert await _all_rows(factory) == before, "data must be rolled back"


# --- 5. idempotency -------------------------------------------------------


async def test_second_run_reports_nothing_to_do(db, isolated_output, capsys):
    """Re-running after a successful backfill is a no-op."""
    url, factory = db
    await bru._run(execute=True, assume_yes=True, database_url=url)
    capsys.readouterr()

    exit_code = await bru._run(execute=False, assume_yes=False, database_url=url)
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "rows that would change: 0" in out
    assert "Nothing to do" in out


async def test_second_execute_writes_no_new_rollback_file(db, isolated_output):
    """With nothing to change, --execute short-circuits before writing anything."""
    url, _ = db
    await bru._run(execute=True, assume_yes=True, database_url=url)
    after_first = list(isolated_output.glob("*.json"))
    assert len(after_first) == 1

    exit_code = await bru._run(execute=True, assume_yes=True, database_url=url)

    assert exit_code == 0
    assert list(isolated_output.glob("*.json")) == after_first


# --- CLI surface ----------------------------------------------------------


def test_yes_without_execute_is_rejected():
    """--yes alone can't be a typo that silently runs a dry run."""
    with pytest.raises(SystemExit) as exc:
        bru.main(["--yes"])
    assert exc.value.code == 2


def test_redact_hides_the_password():
    assert bru._redact("postgresql+asyncpg://confmail:s3cret@db:5432/confmail") == (
        "postgresql+asyncpg://confmail:***@db:5432/confmail"
    )
    # A URL with no credentials is returned untouched.
    assert bru._redact("sqlite+aiosqlite:///./x.db") == "sqlite+aiosqlite:///./x.db"


# --- bucketing ------------------------------------------------------------


@pytest.mark.parametrize(
    "days,expected_bucket",
    [
        (0.0, "same-day (<1d)"),
        (0.9, "same-day (<1d)"),
        (1.0, "1-30 days"),
        (29.9, "1-30 days"),
        (30.0, "30-180 days"),
        (179.9, "30-180 days"),
        (180.0, "180-400 days"),
        (399.9, "180-400 days"),
        (400.0, "400+ days"),
        (972.4, "400+ days"),
    ],
)
def test_histogram_bucket_boundaries(days, expected_bucket):
    """Boundaries are half-open [low, high) so no row lands in two buckets."""
    row = bru.AffectedRow(
        email_id=1,
        received_at=IMPORT_AT,
        zendesk_created_at=IMPORT_AT - timedelta(days=days),
    )
    counts = bru._histogram([row])
    assert counts[expected_bucket] == 1
    assert sum(counts.values()) == 1
