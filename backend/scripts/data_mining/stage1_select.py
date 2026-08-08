"""Stage 1 test-sample selection — deliberate 25-thread spread from Marc's corpus.

Picks a STRUCTURED sample (not random) so the extraction prompt is exercised
against the shapes that actually stress it: one-shot answers, long multi-round
threads, threads carrying internal notes, and threads another agent also worked.
Only the final filler category is random, and it is seeded so the sample is
reproducible.

Read-only: opens ``data/tickets/marc_threads.jsonl`` and writes nothing under
``data/tickets/``. The sample it emits lands in ``data/mining/`` (gitignored —
thread bodies are PII-bearing).

Usage:
    python scripts/data_mining/stage1_select.py
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

# Repo root: backend/scripts/data_mining/ -> up 3.
_ROOT = Path(__file__).resolve().parents[3]
_CORPUS = _ROOT / "data" / "tickets" / "marc_threads.jsonl"
_OUT_DIR = _ROOT / "data" / "mining" / "stage1_test"

PER_CATEGORY = 5
RANDOM_SEED = 7  # matches the app's DRAFTER_SEED convention

# Category order is load-bearing: assignment is greedy and exclusive, so a thread
# matching several predicates lands in the FIRST one listed here. The rarest /
# most specific shapes therefore come first, otherwise a broad category (like
# "1-2 comments") would starve a narrow one (like "another agent replied").
CATEGORIES: list[tuple[str, str]] = [
    ("other_agent_public", "another agent (not Marc, not requester) has a public comment"),
    ("has_internal_note", "contains >= 1 non-public comment"),
    ("long_thread", "8+ total comments"),
    ("short_thread", "1-2 total comments"),
    ("random_remainder", "random pick from everything left"),
]


def load_threads(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def _has_other_agent_public(t: dict) -> bool:
    req = t.get("requester_id")
    return any(
        c.get("public") and not c.get("is_marc") and c.get("author_id") != req
        for c in t["comments"]
    )


def _has_internal_note(t: dict) -> bool:
    return any(c.get("public") is False for c in t["comments"])


def _is_long(t: dict) -> bool:
    return len(t["comments"]) >= 8


def _is_short(t: dict) -> bool:
    return len(t["comments"]) <= 2


PREDICATES = {
    "other_agent_public": _has_other_agent_public,
    "has_internal_note": _has_internal_note,
    "long_thread": _is_long,
    "short_thread": _is_short,
    "random_remainder": lambda t: True,
}


def _spread(pool: list[dict], n: int) -> list[dict]:
    """Take n items evenly spaced across the pool (deterministic, not head-biased).

    Sorting by ticket_id then striding avoids the "first 5 by id" bias, which on
    this corpus would pull five threads from the same few days of 2024.
    """
    pool = sorted(pool, key=lambda t: t["ticket_id"])
    if len(pool) <= n:
        return pool
    step = len(pool) / n
    return [pool[int(i * step)] for i in range(n)]


def select(threads: list[dict], per_category: int = PER_CATEGORY) -> dict[str, list[dict]]:
    """Assign threads to categories greedily and exclusively, in CATEGORIES order."""
    taken: set[int] = set()
    picked: dict[str, list[dict]] = {}
    rng = random.Random(RANDOM_SEED)

    for name, _desc in CATEGORIES:
        pool = [t for t in threads if t["ticket_id"] not in taken and PREDICATES[name](t)]
        if name == "random_remainder":
            chosen = rng.sample(pool, min(per_category, len(pool)))
            chosen.sort(key=lambda t: t["ticket_id"])
        else:
            chosen = _spread(pool, per_category)
        picked[name] = chosen
        taken.update(t["ticket_id"] for t in chosen)
    return picked


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-category", type=int, default=PER_CATEGORY)
    ap.add_argument("--out", type=Path, default=_OUT_DIR / "sample.json")
    args = ap.parse_args()

    threads = load_threads(_CORPUS)
    picked = select(threads, args.per_category)

    total = 0
    for name, desc in CATEGORIES:
        rows = picked[name]
        total += len(rows)
        print(f"\n{name}  ({desc})  -> {len(rows)}")
        for t in rows:
            n_int = sum(1 for c in t["comments"] if c.get("public") is False)
            print(
                f"  {t['ticket_id']:>6}  comments={len(t['comments']):>2} "
                f"internal={n_int}  {t['created_at'][:10]}  {t['subject'][:58]!r}"
            )
    print(f"\nTOTAL SELECTED: {total}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    flat = [{"category": name, **t} for name, _ in CATEGORIES for t in picked[name]]
    args.out.write_text(json.dumps(flat, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"sample written -> {args.out}")


if __name__ == "__main__":
    main()
