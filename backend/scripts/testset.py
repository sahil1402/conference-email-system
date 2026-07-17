"""Curated 20-email test set: build, draft offline, render.

Covers every major real-traffic subject (LLM-labeled intents of the 202-ticket
eval sample) with a fixed quota per (intent, policy_answerable) cell, so the
set exercises both complete answers and [CHAIR: ...]-placeholder drafts.
Selection is deterministic: within each cell, tickets are sorted by id and the
first k with a body in [200, 3000] chars win (falling back to any length if a
cell runs short). Ticket text is used raw — the system is tested on exactly
what requesters wrote.

Drafting runs the offline pipeline (keyword classify -> fusion retrieval over
the real KB -> rule-based route -> drafter with style guide v2) — no app
server involved.

Outputs (gitignored, real-ticket PII):
    data/eval_real/testset_20.jsonl         the curated emails
    data/eval_real/testset_20_drafts.jsonl  pipeline drafts
    data/eval_real/testset_20_report.md     side-by-side readable report

Usage:
    python scripts/testset.py curate
    python scripts/testset.py drafts [--model <id>]
    python scripts/testset.py render
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
from app.pipeline.drafter import ResponseDrafter  # noqa: E402
from app.pipeline.router import EmailRouter  # noqa: E402
from distill_style_guide import read_key  # noqa: E402
from draft_eval import EVAL_DIR, LABELS_PATH, SAMPLE_PATH, Retriever  # noqa: E402

SET_PATH = EVAL_DIR / "testset_20.jsonl"
DRAFTS_PATH = EVAL_DIR / "testset_20_drafts.jsonl"
REPORT_PATH = EVAL_DIR / "testset_20_report.md"
DEFAULT_MODEL = "gpt-5.5"
CONCURRENCY = 5

# (intent, policy_answerable) -> how many tickets. Mirrors real traffic shares
# while guaranteeing every major subject appears; 9 answerable / 11 not.
QUOTA = {
    ("review_assignment", True): 2,
    ("review_assignment", False): 2,
    ("technical_issue", False): 3,  # 0 answerable exist in the sample
    ("formatting_requirements", True): 2,
    ("formatting_requirements", False): 1,
    ("ethics_concern", True): 1,
    ("ethics_concern", False): 1,
    ("general_inquiry", True): 1,
    ("general_inquiry", False): 1,
    ("other", True): 1,
    ("other", False): 1,
    ("submission_withdrawal", False): 2,  # 0 answerable exist in the sample
    ("authorship_dispute", True): 1,
    ("submission_deadline", True): 1,
}


def curate() -> None:
    samples = {r["ticket_id"]: r for r in map(json.loads, open(SAMPLE_PATH, encoding="utf-8"))}
    labels = [json.loads(l) for l in open(LABELS_PATH, encoding="utf-8")]
    picked: list[dict] = []
    for (intent, answerable), k in QUOTA.items():
        cell = sorted(
            (l for l in labels
             if l["intent"] == intent
             and bool(l["policy_answerable"]) == answerable
             and l["ticket_id"] in samples),
            key=lambda l: l["ticket_id"],
        )
        prefer = [l for l in cell if 200 <= len(samples[l["ticket_id"]]["question"]) <= 3000]
        chosen = (prefer + [l for l in cell if l not in prefer])[:k]
        if len(chosen) < k:
            print(f"WARNING: cell ({intent}, answerable={answerable}) has only "
                  f"{len(chosen)} of {k} requested tickets")
        for l in chosen:
            s = samples[l["ticket_id"]]
            picked.append({
                "ticket_id": l["ticket_id"],
                "intent": intent,
                "policy_answerable": answerable,
                "gold_chunk_ids": l["relevant_chunk_ids"],
                "subject": s["subject"],
                "body": s["question"],
                "chair_reply": s["reply"],
            })
    with open(SET_PATH, "w", encoding="utf-8") as fh:
        for row in picked:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"curated {len(picked)} emails -> {SET_PATH}")


async def drafts(model: str) -> None:
    settings.LOCAL_MODEL_BASE_URL = "https://api.openai.com/v1"
    settings.LOCAL_MODEL_NAME = model
    settings.LOCAL_MODEL_API_KEY = read_key()
    settings.DRAFTER_MAX_TOKENS = 2500
    settings.STYLE_GUIDE_PATH = str(REPO_ROOT / "data" / "style_guide" / "style_guide_v2.md")

    rows = [json.loads(l) for l in open(SET_PATH, encoding="utf-8")]
    done = set()
    if DRAFTS_PATH.exists():
        done = {d["ticket_id"] for d in map(json.loads, open(DRAFTS_PATH, encoding="utf-8"))}
    batch = [r for r in rows if r["ticket_id"] not in done]
    retriever = Retriever("fusion")
    router = EmailRouter(strategy="rule_based")
    sem = asyncio.Semaphore(CONCURRENCY)
    lock = asyncio.Lock()
    print(f"testset drafts: {len(batch)} to do ({len(done)} already done)")

    async def one(row: dict) -> None:
        email = {
            "from": "requester@example.org",
            "subject": row["subject"],
            "body": row["body"],
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
        lane = routing.lane
        if draft.placeholders and lane != "human_review":
            lane = "human_review"  # mirrors the orchestrator's downgrade
        rec = {
            "ticket_id": row["ticket_id"],
            "intent": row["intent"],
            "policy_answerable": row["policy_answerable"],
            "lane": lane,
            "draft_text": draft.draft_text,
            "placeholders": draft.placeholders,
            "notes_for_chair": draft.notes_for_chair,
            "reply_leaks": draft.generation_metadata.get("reply_leaks", []),
            "citations": draft.citations,
            "retrieved_ids": [c.policy_id for c in chunks],
            "error": draft.generation_metadata.get("error"),
        }
        async with lock:
            with open(DRAFTS_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    await asyncio.gather(*(one(r) for r in batch))
    print(f"drafts -> {DRAFTS_PATH}")


def render() -> None:
    rows = {r["ticket_id"]: r for r in map(json.loads, open(SET_PATH, encoding="utf-8"))}
    drafted = [json.loads(l) for l in open(DRAFTS_PATH, encoding="utf-8")]
    drafted.sort(key=lambda d: (d["intent"], d["ticket_id"]))

    def trunc(text: str, n: int = 1500) -> str:
        text = text.strip()
        return text if len(text) <= n else text[:n] + f"\n… [truncated, {len(text)} chars total]"

    out = ["# Curated 20-email test set — pipeline drafts\n"]
    out.append("Raw ticket text (unscrubbed); drafts from the offline pipeline "
               "(keyword classify → fusion retrieval → rule-based route → drafter, "
               "style guide v2).\n")
    for d in drafted:
        r = rows[d["ticket_id"]]
        out.append(f"\n---\n\n## Ticket {d['ticket_id']} — {d['intent']} "
                   f"({'answerable' if d['policy_answerable'] else 'not answerable'}, "
                   f"lane: {d['lane']}, placeholders: {len(d['placeholders'] or [])})\n")
        out.append(f"**Subject:** {r['subject']}\n")
        out.append(f"**Inquiry:**\n\n```\n{trunc(r['body'])}\n```\n")
        out.append(f"**Draft:**\n\n```\n{d['draft_text']}\n```\n")
        if d.get("notes_for_chair"):
            out.append(f"**Chair suggestions:**\n\n```\n{d['notes_for_chair']}\n```\n")
        out.append(f"**Chair's real reply (reference):**\n\n```\n{trunc(r['chair_reply'])}\n```\n")
    REPORT_PATH.write_text("\n".join(out), encoding="utf-8")
    print(f"report -> {REPORT_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stage", choices=["curate", "drafts", "render"])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    if args.stage == "curate":
        curate()
    elif args.stage == "drafts":
        asyncio.run(drafts(args.model))
    else:
        render()


if __name__ == "__main__":
    main()
