"""Phase 0b — extract inbound questions from ALL tickets (conference-wide).

For the blind taxonomy induction: unlike Phase 0 (Marc's answered threads only),
this pulls the opening inbound message of every ticket in tickets.jsonl, so the
induced taxonomy reflects the whole help-desk, not one chair's slice.

Input (gitignored PII): data/tickets/tickets.jsonl (21,219 tickets). Uses the
ticket ``description`` (Zendesk = first comment body) as the inbound question.

Output (gitignored): data/mining/all_inbound.jsonl — {ticket_id, year, cycle,
subject, tags, question}. Drops Zendesk sample tickets, workflow-noise-tagged
tickets, and empty/too-short descriptions.

No model calls. Usage:  cd backend && python scripts/mine_extract_all.py
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from scripts.data_mining.mine_extract_marc import clean_body, EXCLUDED_TAGS, CYCLE_BY_YEAR  # noqa: E402

TICKETS_PATH = REPO_ROOT / "data" / "tickets" / "tickets.jsonl"
OUT_PATH = REPO_ROOT / "data" / "mining" / "all_inbound.jsonl"
MIN_CHARS = 20


def main() -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    kept = drop_sample = drop_noise = drop_short = 0
    by_cycle: dict[str, int] = {}
    with open(TICKETS_PATH, encoding="utf-8") as f, open(OUT_PATH, "w", encoding="utf-8") as out:
        for line in f:
            t = json.loads(line)
            via = (t.get("via") or {}).get("channel", "")
            if via == "sample_ticket":
                drop_sample += 1
                continue
            tags = list(t.get("tags") or [])
            if EXCLUDED_TAGS & set(tags):
                drop_noise += 1
                continue
            q = clean_body(t.get("description") or "")
            if len(q) < MIN_CHARS:
                drop_short += 1
                continue
            year = (t.get("created_at") or "")[:4]
            cycle = CYCLE_BY_YEAR.get(year, "other")
            by_cycle[cycle] = by_cycle.get(cycle, 0) + 1
            out.write(json.dumps({
                "ticket_id": t["id"],
                "year": year,
                "cycle": cycle,
                "subject": t.get("subject") or t.get("raw_subject") or "",
                "tags": tags,
                "question": q,
            }) + "\n")
            kept += 1
    print(f"kept {kept}  (dropped: sample {drop_sample}, noise-tag {drop_noise}, too-short {drop_short})")
    print(f"by cycle: {dict(sorted(by_cycle.items()))}")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
