"""READ-ONLY split: divides a confirmed findings report into two files by a
UTC cutoff timestamp, so a priority subset can be republished first.

Context: Marc confirmed the 51-item batch
(internal_notes_20260805_015010_new_only.json) but asked that everything
from 2026-08-03T00:00:00Z onward go out FIRST, since some of those replies
are time-sensitive. republish_internal_notes.py has no built-in date filter
and always processes the entire report file it's given -- so we split the
confirmed report into two files up front, rather than editing the republish
script itself.

This does not touch Zendesk. It only reads one local JSON file and writes
two more.

Usage:
    python scripts/recovery/split_by_date.py \\
        --report scripts/recovery/output/internal_notes_20260805_015010_new_only.json \\
        --cutoff 2026-08-03T00:00:00Z
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def parse_iso(ts: str) -> datetime:
    # Comment timestamps from Zendesk are ISO 8601 with a trailing "Z".
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--cutoff", type=str, required=True,
                         help="UTC ISO timestamp, e.g. 2026-08-03T00:00:00Z. "
                              "Findings at/after this go to the '_urgent' file; "
                              "earlier ones go to the '_remainder' file.")
    args = parser.parse_args()

    cutoff_dt = parse_iso(args.cutoff)
    if cutoff_dt.tzinfo is None:
        cutoff_dt = cutoff_dt.replace(tzinfo=timezone.utc)

    data = json.loads(args.report.read_text(encoding="utf-8"))
    findings = data.get("findings", [])

    urgent, remainder = [], []
    for f in findings:
        created = f.get("created_at")
        if created and parse_iso(created) >= cutoff_dt:
            urgent.append(f)
        else:
            remainder.append(f)

    print(f"Total findings   : {len(findings)}")
    print(f"Urgent  (>= cutoff): {len(urgent)}")
    print(f"Remainder (< cutoff): {len(remainder)}")

    def write(subset: list[dict], suffix: str) -> Path:
        out = dict(data)
        out["findings"] = subset
        out["affected_ticket_ids"] = sorted({f.get("ticket_id") for f in subset})
        if "stats" in out:
            out["stats"] = dict(out["stats"])
            out["stats"]["matches"] = len(subset)
            out["stats"]["tickets_with_matches"] = len(out["affected_ticket_ids"])
        path = args.report.with_name(args.report.stem + suffix + ".json")
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    urgent_path = write(urgent, "_urgent")
    remainder_path = write(remainder, "_remainder")

    print(f"\nUrgent file    : {urgent_path}  <-- republish this one first")
    print(f"Remainder file : {remainder_path}  <-- republish after")


if __name__ == "__main__":
    main()
