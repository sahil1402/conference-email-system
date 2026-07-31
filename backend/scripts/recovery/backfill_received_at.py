"""Backfill ``emails.received_at`` from ``emails.zendesk_created_at``.

Incident context
----------------
``Email.received_at`` is declared with only ``server_default=func.now()``, so
until the ingest fix landed it recorded **when our poller inserted the row**, not
when the requester opened the ticket. Every Zendesk ingest path has since been
changed to pass the real instant through, but rows created before that fix still
carry insert time.

The correct historical value is already in the database: ``zendesk_created_at``
has been populated from ``ticket.created_at`` on every new-ticket ingest since
migration ``d2e4f6a8b0c1``, independently of whether ``received_at`` was right.
So this is a pure in-place UPDATE — it needs no Zendesk API calls at all.

Confirmed against production by read-only SQL before this script was written:
3,231 zendesk-sourced rows, all with ``zendesk_created_at`` populated, all
differing from ``received_at``, zero rows that would become NULL, and zero rows
where ``received_at`` precedes ``zendesk_created_at``. The four rows with
700-972 day drift were inspected by hand and are legitimate old closed tickets
from an earlier AAAI cycle, not data errors — they are re-printed on every run
(see OUTLIER_DAYS) so that judgement is renewed by eye rather than assumed.

Safety model
------------
- **Dry-run is the default.** ``--execute`` is required to write anything. In
  dry-run the transaction is additionally set READ ONLY at the database level
  (PostgreSQL), so an accidental write cannot leave the process.
- **Confirmation.** ``--execute`` prints the full dry-run summary first and then
  requires a typed ``yes``, unless ``--yes`` is also passed (for a non-interactive
  run by someone who has already reviewed the summary).
- **Rollback file before the UPDATE.** Every affected row's id and CURRENT
  ``received_at`` are written and ``fsync``-ed to disk BEFORE the UPDATE is
  issued and BEFORE the commit. A crash at any point therefore cannot leave data
  changed without a rollback file on disk: crash before commit rolls the data
  back, crash after commit finds the file already durable.
- **Verify, then commit.** Inside the same transaction the script checks that the
  affected rowcount matches the snapshot and that the drift histogram is now
  empty. Either check failing rolls the whole thing back instead of committing.
- **Column guard.** The compiled UPDATE is inspected before execution and refused
  if it references any column other than ``received_at``/``zendesk_created_at``.
  ``status``, ``zendesk_status`` and every other column are untouchable here.

Why the buckets are computed in Python and not in SQL
-----------------------------------------------------
``received_at - zendesk_created_at`` yields an ``interval`` on PostgreSQL but has
no equivalent on SQLite, and this script has to be testable offline. The affected
set is a few thousand rows, so it is fetched once and bucketed in Python. That
also makes the snapshot, the histogram and the rollback file all come from ONE
consistent read rather than three separate queries that could disagree.

Usage
-----
    cd backend

    # dry run (default) -- reports what would change, writes nothing
    python scripts/recovery/backfill_received_at.py

    # real run -- prints the same summary, then asks for a typed "yes"
    python scripts/recovery/backfill_received_at.py --execute

    # real run, non-interactive (only after reviewing a dry run)
    python scripts/recovery/backfill_received_at.py --execute --yes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine

# Reuse the app's own config + model + URL normalization (recovery-script house
# rule 2: never re-implement what app/ already owns).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.config import settings  # noqa: E402
from app.db.database import _to_async_url  # noqa: E402
from app.db.models import Email  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
ROLLBACK_PREFIX = "backfill_received_at_rollback_"

# Drift buckets, in days. Same shape as the manual SQL used during discovery.
BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("same-day (<1d)", 0.0, 1.0),
    ("1-30 days", 1.0, 30.0),
    ("30-180 days", 30.0, 180.0),
    ("180-400 days", 180.0, 400.0),
    ("400+ days", 400.0, float("inf")),
)
# Rows past this are individually re-printed on EVERY run so a human re-confirms
# them by eye. Four such rows were verified as genuine old-cycle tickets; if that
# count ever changes, the run says so loudly rather than quietly proceeding.
OUTLIER_DAYS = 400.0
EXPECTED_OUTLIERS = 4

# The ONLY columns this script may ever read-for-comparison or write. Anything
# else appearing in the compiled UPDATE is a bug and aborts the run.
#
# ``updated_at`` is allowed ONLY as a self-assignment. It is declared
# ``onupdate=func.now()``, so SQLAlchemy appends ``updated_at=CURRENT_TIMESTAMP``
# to the SET clause of every UPDATE unless an explicit value is supplied — which
# would silently rewrite the "row last genuinely modified" timestamp on all
# 3,231 rows. That column is load-bearing evidence (it is how a previous incident
# proved a sweep had NOT written to a row), so the statement pins it to its own
# current value and the guard below verifies that pin is present and intact.
ALLOWED_UPDATE_COLUMNS = frozenset({"received_at", "zendesk_created_at", "updated_at"})
UPDATED_AT_SELF_ASSIGNMENT = re.compile(r"\bupdated_at\s*=\s*\w+\.updated_at\b")


class ColumnGuardError(RuntimeError):
    """Raised when the UPDATE would touch a column outside the allowed set."""


class VerificationError(RuntimeError):
    """Raised when a post-UPDATE check fails; the transaction is rolled back."""


@dataclass(frozen=True)
class AffectedRow:
    email_id: int
    received_at: datetime
    zendesk_created_at: datetime

    @property
    def drift_days(self) -> float:
        return (self.received_at - self.zendesk_created_at).total_seconds() / 86400.0


def _redact(url: str) -> str:
    """Hide the password in a SQLAlchemy URL before printing it."""
    return re.sub(r"://([^:/@]+):([^@]*)@", r"://\1:***@", url)


def _affected_predicate():
    """The one predicate shared by the snapshot, the UPDATE and the verification.

    ``is_distinct_from`` (not ``!=``) is load-bearing: SQL three-valued logic makes
    ``received_at != zendesk_created_at`` evaluate to NULL when either side is
    NULL, which would silently drop rows from the count. SQLAlchemy renders this
    as ``IS DISTINCT FROM`` on PostgreSQL and ``IS NOT`` on SQLite.

    The ``IS NOT NULL`` arm is what protects rows that never got a
    ``zendesk_created_at`` (pre-migration rows, or any non-Zendesk row): they are
    never matched, so they keep whatever ``received_at`` they have. The backfill
    can therefore never write a NULL.
    """
    return (
        Email.zendesk_created_at.is_not(None)
        & Email.received_at.is_distinct_from(Email.zendesk_created_at)
    )


def _guard_update_columns(compiled_sql: str) -> None:
    """Refuse to run an UPDATE that mentions any column beyond the allowed two.

    Matched on word boundaries, because a substring test would false-positive:
    ``created_at`` is a real (and forbidden) column AND a substring of
    ``zendesk_created_at``.
    """
    forbidden = [
        column.name
        for column in Email.__table__.columns
        if column.name not in ALLOWED_UPDATE_COLUMNS
        and re.search(rf"\b{re.escape(column.name)}\b", compiled_sql)
    ]
    if forbidden:
        raise ColumnGuardError(
            "UPDATE statement references columns outside the allowed set "
            f"{sorted(ALLOWED_UPDATE_COLUMNS)}: {forbidden}\nSQL: {compiled_sql}"
        )
    # updated_at may appear ONLY pinned to itself. If SQLAlchemy's onupdate has
    # slipped back in (updated_at=CURRENT_TIMESTAMP), refuse the run rather than
    # quietly restamping every row's modification time.
    if re.search(r"\bupdated_at\b", compiled_sql) and not UPDATED_AT_SELF_ASSIGNMENT.search(
        compiled_sql
    ):
        raise ColumnGuardError(
            "UPDATE would overwrite updated_at instead of preserving it "
            f"(expected 'updated_at=<table>.updated_at').\nSQL: {compiled_sql}"
        )


async def _snapshot(conn) -> list[AffectedRow]:
    """Read every row the backfill would change, ordered for a stable report."""
    result = await conn.execute(
        select(Email.id, Email.received_at, Email.zendesk_created_at)
        .where(_affected_predicate())
        .order_by(Email.id)
    )
    return [
        AffectedRow(email_id=r[0], received_at=r[1], zendesk_created_at=r[2])
        for r in result.all()
    ]


def _histogram(rows: list[AffectedRow]) -> dict[str, int]:
    counts = {label: 0 for label, _, _ in BUCKETS}
    for row in rows:
        drift = abs(row.drift_days)
        for label, low, high in BUCKETS:
            if low <= drift < high:
                counts[label] += 1
                break
    return counts


def _print_summary(rows: list[AffectedRow], target: str) -> list[AffectedRow]:
    """Print the dry-run report. Returns the outlier rows for the caller."""
    print(f"target database : {target}")
    print(f"predicate       : zendesk_created_at IS NOT NULL "
          f"AND received_at IS DISTINCT FROM zendesk_created_at")
    print()
    print(f"rows that would change: {len(rows)}")

    if not rows:
        print("\nNothing to do - received_at already matches zendesk_created_at "
              "everywhere it is known.")
        return []

    print("\ndrift histogram (|received_at - zendesk_created_at|):")
    counts = _histogram(rows)
    widest = max(counts.values()) or 1
    for label, _, _ in BUCKETS:
        count = counts[label]
        bar = "#" * int(40 * count / widest)
        print(f"   {label:16s} {count:6d}  {bar}")

    drifts = sorted(abs(r.drift_days) for r in rows)
    print(
        f"\n   min={drifts[0]:.2f}d  median={drifts[len(drifts) // 2]:.2f}d  "
        f"p90={drifts[int(0.9 * len(drifts))]:.2f}d  max={drifts[-1]:.2f}d"
    )

    # Sanity direction check: received_at should always be at or after the real
    # ticket creation (we can only ingest a ticket after it exists). A negative
    # drift would mean the source value is suspect, so it is surfaced rather than
    # silently backfilled.
    backwards = [r for r in rows if r.drift_days < 0]
    if backwards:
        print(
            f"\n   !! WARNING: {len(backwards)} row(s) have received_at EARLIER than "
            "zendesk_created_at.\n      That should be impossible for a polled "
            "ticket - inspect before executing:"
        )
        for row in backwards[:10]:
            print(f"        id={row.email_id}  received_at={row.received_at.isoformat()}"
                  f"  zendesk_created_at={row.zendesk_created_at.isoformat()}")

    outliers = [r for r in rows if abs(r.drift_days) >= OUTLIER_DAYS]
    print(f"\nknown long-drift outliers (>= {OUTLIER_DAYS:.0f} days) - "
          "re-confirm these by eye every run:")
    if not outliers:
        print("   (none)")
    for row in sorted(outliers, key=lambda r: -abs(r.drift_days)):
        print(
            f"   id={row.email_id:<8d} drift={abs(row.drift_days):8.1f}d   "
            f"{row.received_at.isoformat()}  ->  {row.zendesk_created_at.isoformat()}"
        )
    if len(outliers) != EXPECTED_OUTLIERS:
        print(
            f"\n   !! NOTE: {len(outliers)} outlier(s) found but {EXPECTED_OUTLIERS} "
            "were verified by hand previously.\n      Re-verify the new ones before "
            "executing - do not assume they are the same rows."
        )
    return outliers


def _write_rollback_file(rows: list[AffectedRow], target: str) -> Path:
    """Persist id + CURRENT received_at for every affected row, durably.

    Called BEFORE the UPDATE and BEFORE the commit, and ``fsync``-ed, so there is
    no window in which committed data has no rollback record on disk.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUTPUT_DIR / f"{ROLLBACK_PREFIX}{stamp}.json"
    payload = {
        "schema_version": 1,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "target_database": target,
        "script": Path(__file__).name,
        "description": (
            "Pre-backfill snapshot of emails.received_at. To roll back, restore "
            "received_at to prior_received_at for each id below."
        ),
        "row_count": len(rows),
        "rows": [
            {
                "id": row.email_id,
                "prior_received_at": row.received_at.isoformat(),
                "new_received_at": row.zendesk_created_at.isoformat(),
            }
            for row in rows
        ],
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    return path


async def _run(execute: bool, assume_yes: bool, database_url: str) -> int:
    async_url = _to_async_url(database_url)
    target = _redact(async_url)
    engine = create_async_engine(async_url, echo=False, future=True)

    try:
        async with engine.connect() as conn:
            is_postgres = conn.dialect.name == "postgresql"
            if not execute and is_postgres:
                # Belt and braces: in dry-run the database itself refuses writes.
                await conn.exec_driver_sql("SET TRANSACTION READ ONLY")

            rows = await _snapshot(conn)
            print("=" * 78)
            print("BACKFILL received_at  <-  zendesk_created_at   "
                  f"[{'EXECUTE' if execute else 'DRY RUN'}]")
            print("=" * 78)
            _print_summary(rows, target)

            if not execute:
                print("\n" + "-" * 78)
                print("DRY RUN - no rollback file written, no UPDATE issued, "
                      "0 rows modified.")
                print("Re-run with --execute to apply.")
                await conn.rollback()
                return 0

            if not rows:
                await conn.rollback()
                return 0

            if not assume_yes:
                print("\n" + "-" * 78)
                print(f"About to UPDATE {len(rows)} rows on {target}.")
                answer = input('Type "yes" to proceed: ').strip()
                if answer != "yes":
                    print("Aborted - nothing was written.")
                    await conn.rollback()
                    return 3

            # --- rollback file FIRST, durable, before any data changes --------
            rollback_path = _write_rollback_file(rows, target)
            print(f"\nrollback file written + fsynced: {rollback_path}")

            statement = (
                update(Email)
                .where(_affected_predicate())
                # updated_at pinned to itself: an explicit value takes precedence
                # over the column's onupdate=func.now(), so the row's real
                # last-modified time survives the backfill untouched.
                .values(
                    received_at=Email.zendesk_created_at,
                    updated_at=Email.updated_at,
                )
            )
            compiled = str(statement.compile(dialect=conn.dialect))
            _guard_update_columns(compiled)
            print(f"guarded SQL     : {compiled}")

            result = await conn.execute(statement)
            affected = result.rowcount or 0
            print(f"rows affected   : {affected}")

            # --- verify INSIDE the transaction, commit only if clean ----------
            if affected != len(rows):
                raise VerificationError(
                    f"expected to update {len(rows)} rows but the UPDATE reported "
                    f"{affected}; rolling back."
                )
            remaining = await _snapshot(conn)
            post_counts = _histogram(remaining)
            print("\npost-update drift histogram (expected: all zero):")
            for label, _, _ in BUCKETS:
                print(f"   {label:16s} {post_counts[label]:6d}")
            if remaining:
                raise VerificationError(
                    f"{len(remaining)} row(s) still differ after the UPDATE; "
                    "rolling back."
                )

            await conn.commit()
            print("\n" + "-" * 78)
            print(f"COMMITTED - {affected} rows updated. "
                  f"Rollback data: {rollback_path}")
            return 0

    except (ColumnGuardError, VerificationError) as exc:
        print(f"\nABORTED (rolled back): {exc}", file=sys.stderr)
        return 4
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill emails.received_at from emails.zendesk_created_at. "
            "Dry-run by default; --execute to write."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform the UPDATE (default is a read-only dry run).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help='Skip the interactive "yes" prompt (only with --execute).',
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override settings.DATABASE_URL (the resolved target is always printed).",
    )
    args = parser.parse_args(argv)

    if args.yes and not args.execute:
        parser.error("--yes is meaningless without --execute")

    database_url = args.database_url or settings.DATABASE_URL
    return asyncio.run(_run(args.execute, args.yes, database_url))


if __name__ == "__main__":
    raise SystemExit(main())
