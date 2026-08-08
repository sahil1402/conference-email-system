"""Pick a topically-spread Stage 2 test batch.

THE PROBLEM: family is exactly what Stage 2 computes, so there is no ground-truth
field to stratify on. Pure random would over-sample whatever dominates the inbox
and might never exercise the small families.

THE COMPROMISE: a deterministic keyword proxy scores each extraction against each
family's own definition text (pooled from INTENT_DEFS — no hand-invented word
lists), and we sample evenly across the resulting buckets.

WHAT THIS PROXY IS NOT:
  - not a label, not ground truth, and never shown to the tagger;
  - used ONLY to choose which tickets to look at.
Consequence: the family distribution of the tagger's OUTPUT on this batch is
biased by construction and says nothing about corpus composition. Only the
full-corpus run can answer that.

Usage:
    python scripts/data_mining/stage2_select.py --per-family 8
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "backend"))

from app.pipeline.taxonomy import FAMILIES, INTENT_DEFS, INTENT_FAMILIES  # noqa: E402

_STAGE1 = _ROOT / "data" / "mining" / "stage1_full" / "results.json"
_OUT = _ROOT / "data" / "mining" / "stage2_test" / "sample_ids.json"
RANDOM_SEED = 7

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "at", "by", "with",
    "is", "are", "was", "were", "be", "been", "it", "its", "that", "this", "as", "from",
    "not", "no", "any", "all", "their", "they", "them", "asked", "requester", "chair",
    "request", "requests", "ticket", "submission", "submissions", "paper", "papers",
    "aaai", "conference", "please", "who", "which", "what", "how", "about", "into",
    "after", "before", "may", "can", "could", "would", "should", "if", "then", "than",
    "one", "two", "more", "other", "same", "own", "via", "per", "out", "up", "down",
}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in _STOP}


def family_vocab() -> dict[str, set[str]]:
    """Pool each family's definition text into a bag of distinctive words.

    Words shared by 3+ families are dropped — they carry no discriminating signal.
    """
    pooled: dict[str, set[str]] = {f: set() for f in FAMILIES}
    for intent, definition in INTENT_DEFS.items():
        pooled[INTENT_FAMILIES[intent]] |= _words(definition)
    counts = Counter(w for words in pooled.values() for w in words)
    return {f: {w for w in words if counts[w] < 3} for f, words in pooled.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-family", type=int, default=8)
    ap.add_argument("--stage1", type=Path, default=_STAGE1)
    ap.add_argument("--out", type=Path, default=_OUT)
    args = ap.parse_args()

    rows = json.loads(args.stage1.read_text(encoding="utf-8"))
    recs = [
        r for r in rows
        if r.get("category") != "merge_closure" and "error" not in r and r.get("what_was_asked")
    ]
    vocab = family_vocab()

    buckets: dict[str, list[tuple[int, int]]] = {f: [] for f in FAMILIES}
    unbucketed = 0
    for r in recs:
        text = _words(r["what_was_asked"]) | _words(" ".join(r.get("steps_taken") or []))
        scores = {f: len(text & v) for f, v in vocab.items()}
        best = max(scores, key=lambda f: scores[f])
        if scores[best] == 0:
            unbucketed += 1
            continue
        buckets[best].append((scores[best], r["ticket_id"]))

    rng = random.Random(RANDOM_SEED)
    picked: dict[str, list[int]] = {}
    for f in FAMILIES:
        # Rank by proxy confidence, then sample from the top slice so picks are
        # clearly on-topic without being the same handful of extreme scores.
        pool = [t for _, t in sorted(buckets[f], reverse=True)[: args.per_family * 6]]
        picked[f] = sorted(rng.sample(pool, min(args.per_family, len(pool))))

    print(f"extractions considered : {len(recs)}")
    print(f"no proxy signal (skipped for selection): {unbucketed}")
    print("\nproxy buckets (SELECTION ONLY — not labels):")
    for f in FAMILIES:
        print(f"  {f:24s} pool={len(buckets[f]):>5}  picked={len(picked[f])}  {picked[f]}")

    ids = sorted(t for v in picked.values() for t in v)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(ids, indent=2), encoding="utf-8")
    (args.out.parent / "sample_proxy_buckets.json").write_text(
        json.dumps(picked, indent=2), encoding="utf-8"
    )
    print(f"\nTOTAL: {len(ids)} ticket ids -> {args.out}")


if __name__ == "__main__":
    main()
