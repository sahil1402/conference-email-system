"""MANUAL diagnostic — does the real LLM obey the CHAIR-SELECTED instruction?

⚠️ NOT A TEST. Makes REAL, BILLED model calls and is non-deterministic. It lives
in ``scripts/`` precisely so pytest never collects it (``testpaths = ["tests"]``
in pyproject.toml) — do not move it under ``tests/`` and do not wire it into CI.

Task 4 marks a chair-forced policy ``[CHAIR-SELECTED — MUST ADDRESS]`` and adds a
narrow instruction overriding "answer only what was asked". The unit tests prove
the PROMPT is built correctly; they cannot prove the model COMPLIES. This script
answers that by drafting real replies and printing them for a human to judge.

READ-ONLY: it consumes JSON dumps of emails/policies and calls the drafter
directly. It never opens a DB session and never writes a draft anywhere.

Usage (from backend/):
    python scripts/manual_forced_policy_compliance.py --data-dir /path/to/dumps

Expects in --data-dir:
    diag_emails.json    [{id, zendesk_ticket_id, subject, body, sender,
                          sender_name, classification, retrieval_context}, ...]
    diag_policies.json  [{policy_key, title, content, category, status}, ...]
"""

import argparse
import asyncio
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.pipeline.classifier import ClassificationResult  # noqa: E402
from app.pipeline.drafter import ResponseDrafter  # noqa: E402
from app.pipeline.retriever import RetrievedChunk  # noqa: E402

# (zendesk_ticket_id, forced policy key). Each forced key is deliberately NOT in
# that ticket's natural top-3, so a mention can only come from the instruction.
CASES = [
    (21010, "policy_189"),   # committee invitation      ← blind review
    (22199, "policy_117"),   # DBLP profile compliance   ← key dates
    (22231, "policy_109"),   # deceased coauthor         ← AI-assisted peer review
    (22449, "policy_153"),   # restore submission        ← reviewer confidentiality
    (22783, "policy_156"),   # paper bidding             ← author responsibilities
]
# Controls: same ticket, NO forced policy — shows whether the topic would have
# been raised anyway (guards against crediting the instruction for a coincidence).
CONTROLS = [22231, 22783]


def _load(data_dir):
    def rd(name):
        with io.open(os.path.join(data_dir, name), encoding="utf-8") as fh:
            return json.load(fh)

    emails = {e["zendesk_ticket_id"]: e for e in rd("diag_emails.json")}
    policies = {p["policy_key"]: p for p in rd("diag_policies.json")}
    return emails, policies


def _chunk(p, score):
    return RetrievedChunk(
        policy_id=p["policy_key"], title=p["title"] or "", content=p["content"] or "",
        score=score, category=p["category"] or "",
    )


def _build(email, policies, forced_key):
    """Reproduce production grounding: ranked top-k, then the forced EXTRA slot."""
    ids = email["retrieval_context"]["retrieved_ids"]
    chunks = [_chunk(policies[i], 1.0 - n * 0.1) for n, i in enumerate(ids) if i in policies]
    if forced_key and not any(c.policy_id == forced_key for c in chunks):
        chunks.append(_chunk(policies[forced_key], 0.0))
    return chunks


async def _run_one(drafter, email, policies, forced_key, label):
    chunks = _build(email, policies, forced_key)
    cls_raw = email["classification"] or {}
    cls = ClassificationResult(
        intent=cls_raw.get("intent") or "unknown",
        confidence=float(cls_raw.get("confidence") or 0.5),
        reasoning=cls_raw.get("reasoning") or "",
        method=cls_raw.get("method") or "keyword",
    )
    payload = {
        "from": email["sender"], "sender_name": email.get("sender_name"),
        "subject": email["subject"], "body": email["body"],
    }
    draft = await drafter.draft(payload, cls, chunks, forced_key)

    print("=" * 78)
    print(f"{label} | ticket #{email['zendesk_ticket_id']} | intent={cls.intent}")
    print(f"SUBJECT : {email['subject']}")
    print(f"NATURAL TOP-K : {email['retrieval_context']['retrieved_ids']}")
    if forced_key:
        print(f"FORCED  : {forced_key} — {policies[forced_key]['title']}")
        print(f"          (excerpt) {policies[forced_key]['content'][:220].strip()}…")
    else:
        print("FORCED  : (none — CONTROL run)")
    print("-" * 78)
    print("REPLY:")
    print(draft.draft_text.strip() or "(empty)")
    print()
    print(f"CITATIONS: {draft.citations}")
    print(f"answer_confidence={draft.answer_confidence} placeholders={len(draft.placeholders)}")
    print()
    return draft


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    args = ap.parse_args()

    emails, policies = _load(args.data_dir)
    drafter = ResponseDrafter(provider=settings.MODEL_PROVIDER)

    total = len(CASES) + len(CONTROLS)
    print(f"PROVIDER={settings.MODEL_PROVIDER} MODEL={settings.LOCAL_MODEL_NAME}")
    print(f"Making {total} REAL model calls. READ-ONLY: no DB session, no writes.\n")

    for ticket, forced in CASES:
        await _run_one(drafter, emails[ticket], policies, forced, "FORCED")
    for ticket in CONTROLS:
        await _run_one(drafter, emails[ticket], policies, None, "CONTROL")


if __name__ == "__main__":
    asyncio.run(main())
