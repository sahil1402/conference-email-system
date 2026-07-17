"""Placeholder-contract validation (Phase 7F).

Re-drafts the policy-answerable eval tickets with the revised drafter prompt +
style guide (config v2, fusion retrieval — the production-recommended setup)
and measures how the reply contract changed behavior vs. the Phase 7D drafts:

  - leak rate: replies containing chair-facing meta language ("the policy does
    not specify...", "we will look into...", "cannot confirm...") — should
    drop to ~0
  - placeholder usage: [CHAIR: ...] tokens now carrying those gaps instead
  - notes coverage: chair suggestions accompanying each placeholder

New drafts go to data/eval_real/drafts_placeholder.jsonl (gitignored;
resumable by ticket_id). The `report` stage compares against the old v2
drafts in drafts.jsonl.

Usage:
    python scripts/placeholder_eval.py drafts [--model <id>]
    python scripts/placeholder_eval.py report
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.config import settings  # noqa: E402
from app.pipeline.classifier import keyword_classify  # noqa: E402
from app.pipeline.drafter import _LEAK_PATTERNS, ResponseDrafter  # noqa: E402
from app.pipeline.router import EmailRouter  # noqa: E402
from distill_style_guide import read_key, scrub  # noqa: E402
from draft_eval import DRAFTS_PATH, EVAL_DIR, Retriever, load_answerable  # noqa: E402

OUT_PATH = EVAL_DIR / "drafts_placeholder.jsonl"
DEFAULT_MODEL = "gpt-5.5"
CONCURRENCY = 5


def leaks_in(text: str) -> list[str]:
    """The drafter's own leak patterns, applied to arbitrary draft text."""
    found: list[str] = []
    for pattern in _LEAK_PATTERNS:
        found.extend(m.group(0) for m in pattern.finditer(text))
    return found


async def generate(model: str) -> None:
    settings.LOCAL_MODEL_BASE_URL = "https://api.openai.com/v1"
    settings.LOCAL_MODEL_NAME = model
    settings.LOCAL_MODEL_API_KEY = read_key()
    settings.DRAFTER_MAX_TOKENS = 2500
    settings.STYLE_GUIDE_PATH = str(
        REPO_ROOT / "data" / "style_guide" / "style_guide_v2.md"
    )

    rows = load_answerable()
    done = set()
    if OUT_PATH.exists():
        done = {d["ticket_id"] for d in map(json.loads, open(OUT_PATH, encoding="utf-8"))}
    batch = [r for r in rows if r["ticket_id"] not in done]
    retriever = Retriever("fusion")
    router = EmailRouter(strategy="rule_based")
    sem = asyncio.Semaphore(CONCURRENCY)
    lock = asyncio.Lock()
    print(f"placeholder drafts: {len(batch)} to do ({len(done)} already done)")

    async def one(row: dict) -> None:
        email = {
            "from": "requester@example.org",
            "subject": scrub(row["subject"]),
            "body": scrub(row["question"]),
        }
        classification = keyword_classify(email["subject"], email["body"])
        chunks = await retriever.retrieve(
            f"{email['subject']} {email['body'][:300]}", "",
            settings.MAX_RETRIEVED_CHUNKS,
        )
        routing = router.route(classification, chunks)
        async with sem:
            draft = await ResponseDrafter(provider="local").draft(
                email, classification, chunks, routing
            )
        rec = {
            "ticket_id": row["ticket_id"],
            "config": "v2_placeholder",
            "draft_text": draft.draft_text,
            "notes_for_chair": draft.notes_for_chair,
            "placeholders": draft.placeholders,
            "reply_leaks": draft.generation_metadata.get("reply_leaks", []),
            "citations": draft.citations,
            "model_used": draft.model_used,
            "error": draft.generation_metadata.get("error"),
            "retrieved_ids": [c.policy_id for c in chunks],
            "gold": row["gold"],
            "lane": routing.lane,
        }
        async with lock:
            with open(OUT_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    await asyncio.gather(*(one(r) for r in batch))
    print(f"drafts -> {OUT_PATH}")


def report() -> None:
    old_v2 = [
        d for d in map(json.loads, open(DRAFTS_PATH, encoding="utf-8"))
        if d["config"] == "v2" and not d.get("error")
    ]
    new = [
        d for d in map(json.loads, open(OUT_PATH, encoding="utf-8"))
        if not d.get("error")
    ]

    def summarize(name: str, rows: list[dict], text_key: str = "draft_text") -> None:
        leaked = [r for r in rows if leaks_in(r[text_key])]
        with_ph = [r for r in rows if r.get("placeholders")]
        n_notes = sum(1 for r in rows if r.get("notes_for_chair"))
        print(f"{name}: n={len(rows)}")
        print(f"  replies with chair-meta leaks: {len(leaked)} "
              f"({100 * len(leaked) / max(len(rows), 1):.0f}%)")
        print(f"  replies with [CHAIR: ...] placeholders: {len(with_ph)}")
        print(f"  total placeholders: {sum(len(r.get('placeholders') or []) for r in rows)}")
        print(f"  drafts with notes_for_chair: {n_notes}")

    summarize("OLD (Phase 7D, guide v2)", old_v2)
    summarize("NEW (placeholder contract, guide v2)", new)

    # Placeholder <-> note pairing: every placeholder should have chair notes.
    unpaired = [r for r in new if r.get("placeholders") and not r.get("notes_for_chair")]
    print(f"NEW drafts with placeholders but NO chair notes: {len(unpaired)}")
    residual = [(r["ticket_id"], leaks_in(r["draft_text"])) for r in new
                if leaks_in(r["draft_text"])]
    for tid, leaks in residual:
        print(f"  residual leak ticket {tid}: {leaks}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stage", choices=["drafts", "report"])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    if args.stage == "drafts":
        asyncio.run(generate(args.model))
    else:
        report()


if __name__ == "__main__":
    main()
