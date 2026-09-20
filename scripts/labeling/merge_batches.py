#!/usr/bin/env python
"""Merge finished labeling batches back into the main Phase 1 set.

Run BY HAND, once both labelers are done. Nothing calls this automatically.

    python scripts/labeling/merge_batches.py             # dry run (default)
    python scripts/labeling/merge_batches.py --execute   # actually write

Dry-run by default, following the house rule for scripts that mutate data a
human produced (cf. scripts/recovery/backfill_received_at.py): the main file
represents hours of irreplaceable manual judgment, and a merge that silently
clobbered it would be unrecoverable without a re-label.

Three label states, ranked, and a merge only ever moves a row UP this ladder:

    2  labeled    (is_reject_appeal is not None)
    1  deferred   (deferred true, labels still null)
    0  untouched

That ranking is what implements "never overwrite a real label with an
untouched one" -- it is a rule about the two VALUES, not about which file is
newer, so re-running the merge is idempotent and batch order does not matter.

A genuine CONFLICT is only ever two sources that both sit at rank 2 and
disagree on (is_reject_appeal, appeal_reason). Deferral never conflicts with
anything: it is an absence of judgment, so a real label simply supersedes it.
On any conflict the script writes NOTHING and exits non-zero -- a partial
merge would leave the main file in a state no one could reason about.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from label_appeals import (  # noqa: E402
    DATA_PATH,
    DEFERRED_KEY,
    NOT_APPEAL,
    REASONS,
    load,
    save,
)

BATCH_GLOB = "batch_*.jsonl"
LABEL_FIELDS = ("is_reject_appeal", "appeal_reason", DEFERRED_KEY)


def rank(record: dict) -> int:
    """0 untouched / 1 deferred / 2 labeled. See module docstring."""
    if record.get("is_reject_appeal") is not None:
        return 2
    return 1 if record.get(DEFERRED_KEY) else 0


def decision(record: dict) -> tuple:
    """The judgment itself, for comparing two rank-2 rows."""
    return (record.get("is_reject_appeal"), record.get("appeal_reason"))


def describe(record: dict) -> str:
    if rank(record) == 2:
        if not record.get("is_reject_appeal"):
            return "not a reject appeal"
        reason = record.get("appeal_reason")
        return "appeal [{}] {}".format(reason, REASONS.get(reason, "?"))
    return "deferred" if rank(record) == 1 else "untouched"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge data/labeling/batch_*.jsonl back into the main set.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually write the merged result. Without this, only report.",
    )
    parser.add_argument(
        "--main",
        type=Path,
        default=DATA_PATH,
        help="Main file to merge into (default: the full Phase 1 set).",
    )
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if not args.main.exists():
        print("ERROR: main file not found: {}".format(args.main), file=sys.stderr)
        return 2

    records, _ = load(args.main)
    by_id = {r["ticket_id"]: r for r in records}

    batch_paths = sorted(args.main.parent.glob(BATCH_GLOB))
    if not batch_paths:
        print("No {} files found in {}".format(BATCH_GLOB, args.main.parent))
        return 0

    print("main : {} ({} rows)".format(args.main.name, len(records)))
    for path in batch_paths:
        print("batch: {}".format(path.name))
    print()

    # --- collect proposals -------------------------------------------------
    # ticket_id -> list of (batch name, row). Gathered from EVERY batch before
    # anything is applied, so a batch-vs-batch disagreement is caught even
    # though the current batches are disjoint by construction.
    proposals: dict[int, list[tuple[str, dict]]] = {}
    unknown: list[tuple[str, int]] = []
    per_batch_seen: Counter = Counter()

    for path in batch_paths:
        batch_records, _ = load(path)
        for row in batch_records:
            ticket_id = row["ticket_id"]
            if ticket_id not in by_id:
                unknown.append((path.name, ticket_id))
                continue
            per_batch_seen[path.name] += 1
            if rank(row) > 0:
                proposals.setdefault(ticket_id, []).append((path.name, row))

    if unknown:
        print("ERROR: batch rows whose ticket_id is absent from the main file:",
              file=sys.stderr)
        for name, ticket_id in unknown[:20]:
            print("  {} -> ticket {}".format(name, ticket_id), file=sys.stderr)
        print("Refusing to merge; nothing written.", file=sys.stderr)
        return 3

    # --- conflict detection ------------------------------------------------
    conflicts: list[str] = []
    for ticket_id, entries in sorted(proposals.items()):
        decided = [(name, row) for name, row in entries if rank(row) == 2]
        distinct = {decision(row) for _, row in decided}
        if len(distinct) > 1:
            detail = "; ".join(
                "{} says {}".format(name, describe(row)) for name, row in decided
            )
            conflicts.append("ticket {}: {}".format(ticket_id, detail))
            continue
        # A batch decision that contradicts one already recorded in main.
        current = by_id[ticket_id]
        if decided and rank(current) == 2 and decision(current) != next(iter(distinct)):
            name, row = decided[0]
            conflicts.append(
                "ticket {}: main says {}; {} says {}".format(
                    ticket_id, describe(current), name, describe(row)
                )
            )

    if conflicts:
        print("CONFLICT - the same ticket is labeled differently in two places:",
              file=sys.stderr)
        for line in conflicts:
            print("  " + line, file=sys.stderr)
        print(
            "\nResolve by hand (edit one side so they agree, or delete the "
            "losing row from its batch), then re-run.\nNothing was written.",
            file=sys.stderr,
        )
        return 4

    # --- apply (in memory) -------------------------------------------------
    merged: Counter = Counter()
    already: Counter = Counter()
    skipped_lower: Counter = Counter()

    for ticket_id, entries in sorted(proposals.items()):
        current = by_id[ticket_id]
        # Highest-ranked proposal wins; ties are identical by the check above.
        name, row = max(entries, key=lambda e: rank(e[1]))
        if rank(row) <= rank(current):
            (already if rank(row) == rank(current) else skipped_lower)[name] += 1
            continue
        for field in LABEL_FIELDS:
            current[field] = row.get(field, False if field == DEFERRED_KEY else None)
        merged[name] += 1

    print("Merged rows, by batch:")
    for path in batch_paths:
        name = path.name
        print("  {:<24} {:>4} merged   ({} rows scanned, {} already in main,"
              " {} weaker than main)".format(
                  name, merged[name], per_batch_seen[name],
                  already[name], skipped_lower[name]))
    total = sum(merged.values())

    labeled = sum(1 for r in records if r.get("is_reject_appeal") is not None)
    deferred = sum(
        1 for r in records
        if r.get("is_reject_appeal") is None and r.get(DEFERRED_KEY)
    )
    reasons = Counter(
        r.get("appeal_reason") if r.get("is_reject_appeal") else NOT_APPEAL
        for r in records
        if r.get("is_reject_appeal") is not None
    )
    print("\nResulting main file would be:")
    print("  labeled   {} / {}".format(labeled, len(records)))
    for key in (NOT_APPEAL,) + tuple(REASONS):
        if reasons.get(key):
            label = "not a reject appeal" if key == NOT_APPEAL else REASONS[key]
            print("    [{}] {:<28} {}".format(key, label, reasons[key]))
    print("  deferred  {} / {}".format(deferred, len(records)))
    print("  untouched {} / {}".format(len(records) - labeled - deferred, len(records)))

    if not args.execute:
        print("\nDRY RUN - nothing written. Re-run with --execute to apply.")
        return 0
    if total == 0:
        print("\nNothing to merge; main file left untouched.")
        return 0

    save(records, args.main)
    print("\nWrote {} merged rows to {}".format(total, args.main))
    return 0


if __name__ == "__main__":
    sys.exit(main())
