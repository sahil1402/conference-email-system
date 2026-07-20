"""Phase 0 — extract clean Marc Q->A pairs from the Zendesk export.

Input (gitignored, real PII): data/tickets/marc_threads.jsonl (4,094 threads where
Marc Pujol-Gonzalez posted a public reply) + data/tickets/tickets.jsonl (for the
chair-applied Zendesk tags, which are sparse on-thread).

Output (gitignored): data/mining/marc_qa.jsonl — one row per usable thread:
  {ticket_id, year, cycle, subject, tags, question, answer}
where question = the requester's opening public message and answer = Marc's first
public reply. Threads tagged with pure workflow noise are dropped (reusing
label_real_tickets.EXCLUDED_TAGS).

No model calls. Run anywhere. PII stays in the gitignored output.

Usage:  cd backend && python scripts/mine_extract_marc.py
"""
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

TICKETS_DIR = REPO_ROOT / "data" / "tickets"
THREADS_PATH = TICKETS_DIR / "marc_threads.jsonl"
TICKETS_PATH = TICKETS_DIR / "tickets.jsonl"
OUT_DIR = REPO_ROOT / "data" / "mining"
OUT_PATH = OUT_DIR / "marc_qa.jsonl"

# Reuse the noise filter from the existing labeling script (kept in sync manually
# to avoid importing its heavier deps).
EXCLUDED_TAGS = {"closed_by_merge", "system_email_notification_failure"}

# 2025 tickets are the AAAI-26 cycle; 2026 tickets are AAAI-27 (matches E001/E005).
CYCLE_BY_YEAR = {"2024": "pre", "2025": "AAAI-26", "2026": "AAAI-27"}

# Reply-chain / signature markers: everything from the first match onward is
# quoted history or boilerplate, not the sender's actual ask.
_CUT_MARKERS = [
    re.compile(r"^\s*On .+ wrote:\s*$", re.MULTILINE),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^\s*From:\s.+$", re.MULTILINE),
    re.compile(r"^\s*_{5,}\s*$", re.MULTILINE),
    re.compile(r"^\s*Sent from my ", re.MULTILINE),
]
_QUOTE_LINE = re.compile(r"^\s*>.*$", re.MULTILINE)
_WS = re.compile(r"[ \t]+")
_NL = re.compile(r"\n{3,}")


def clean_body(text: str, cap: int = 2000) -> str:
    """Strip quoted history / signatures / excess whitespace; cap length."""
    if not text:
        return ""
    cut = len(text)
    for pat in _CUT_MARKERS:
        m = pat.search(text)
        if m:
            cut = min(cut, m.start())
    text = text[:cut]
    text = _QUOTE_LINE.sub("", text)
    text = _WS.sub(" ", text)
    text = _NL.sub("\n\n", text).strip()
    return text[:cap]


def load_ticket_tags() -> dict[int, list[str]]:
    """Map ticket_id -> Zendesk tags from tickets.jsonl (richer than on-thread)."""
    out: dict[int, list[str]] = {}
    with open(TICKETS_PATH, encoding="utf-8") as f:
        for line in f:
            t = json.loads(line)
            if t.get("tags"):
                out[t["id"]] = list(t["tags"])
    return out


def main() -> None:
    ticket_tags = load_ticket_tags()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    kept = dropped_noise = dropped_shape = 0
    by_cycle: dict[str, int] = {}
    with open(THREADS_PATH, encoding="utf-8") as f, open(OUT_PATH, "w", encoding="utf-8") as out:
        for line in f:
            th = json.loads(line)
            tid = th["ticket_id"]
            tags = sorted(set(th.get("tags") or []) | set(ticket_tags.get(tid, [])))
            if EXCLUDED_TAGS & set(tags):
                dropped_noise += 1
                continue

            comments = th.get("comments") or []
            question = next(
                (c["body"] for c in comments if not c.get("is_marc") and c.get("public")),
                None,
            )
            answer = next((c["body"] for c in comments if c.get("is_marc")), None)
            q, a = clean_body(question or ""), clean_body(answer or "")
            if not q or not a:
                dropped_shape += 1
                continue

            year = (th.get("created_at") or "")[:4]
            cycle = CYCLE_BY_YEAR.get(year, "other")
            by_cycle[cycle] = by_cycle.get(cycle, 0) + 1
            out.write(json.dumps({
                "ticket_id": tid,
                "year": year,
                "cycle": cycle,
                "subject": th.get("subject") or "",
                "tags": tags,
                "question": q,
                "answer": a,
            }) + "\n")
            kept += 1

    print(f"kept {kept}  (dropped: noise-tag {dropped_noise}, no-Q-or-A {dropped_shape})")
    print(f"by cycle: {dict(sorted(by_cycle.items()))}")
    print(f"-> {OUT_PATH}")


if __name__ == "__main__":
    main()
