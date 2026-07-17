"""Backfill the app queue from EXISTING eval drafts — zero drafting-API calls.

For every policy-answerable ticket in the eval sample whose style-guide-v2
draft already exists in data/eval_real/drafts.jsonl, run the email through the
REAL pipeline (classify -> retrieve -> route -> chair-assign -> persist ->
audit) with the drafter stubbed to return the stored draft. Tickets already
ingested live (sender ticket-<id>@sample.aaai.local present in the DB) are
skipped, so this composes with a partial live ingest.

Legacy eval drafts predate the structured-output change; their trailing
"Chair note:" blocks are split into notes_for_chair and the reply is passed
through the drafter's sanitizer (scaffold headers + internal policy ids
stripped) so backfilled records match current drafter behavior.

Run with the backend up or down (brief writes):
    cd backend && python scripts/backfill_eval_drafts.py
"""

import asyncio
import json
import re
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.db.database import async_session_factory  # noqa: E402
from app.db.models import Email  # noqa: E402
from app.pipeline.drafter import DraftResponse, _sanitize_reply  # noqa: E402
from app.pipeline.orchestrator import EmailPipeline  # noqa: E402

EVAL_DIR = _ROOT_DIR / "data" / "eval_real"

_CHAIR_NOTE_RE = re.compile(r"\n+\s*Chair note:\s*", re.IGNORECASE)


def _clean_legacy(draft_text: str) -> tuple[str, str | None]:
    """Split a legacy eval draft into (sanitized reply, notes_for_chair)."""
    parts = _CHAIR_NOTE_RE.split(draft_text, maxsplit=1)
    reply = _sanitize_reply(parts[0])
    notes = parts[1].strip() if len(parts) > 1 else None
    return reply, notes or None


class StoredDrafter:
    """Drop-in drafter returning a pre-generated eval draft. No network."""

    provider = "eval_backfill"  # read by the orchestrator's tracer stage

    def __init__(self, record: dict) -> None:
        self._record = record

    async def draft(self, email, classification, retrieved_chunks, routing) -> DraftResponse:
        reply, notes = _clean_legacy(self._record["draft_text"])
        return DraftResponse(
            draft_text=reply,
            notes_for_chair=notes,
            citations=self._record.get("citations") or [],
            model_used=self._record.get("model_used") or "unknown",
            generation_metadata={"lane": routing.lane, "provider": "eval_backfill"},
        )


async def main() -> None:
    samples = {r["ticket_id"]: r for r in map(json.loads, open(EVAL_DIR / "sample.jsonl", encoding="utf-8"))}
    answerable = {
        l["ticket_id"]
        for l in map(json.loads, open(EVAL_DIR / "labels.jsonl", encoding="utf-8"))
        if l["policy_answerable"] and l["relevant_chunk_ids"]
    }
    v2_drafts = {
        d["ticket_id"]: d
        for d in map(json.loads, open(EVAL_DIR / "drafts.jsonl", encoding="utf-8"))
        if d["config"] == "v2" and not d.get("error")
    }

    async with async_session_factory() as db:
        rows = (await db.execute(select(Email.sender))).scalars().all()
        present = {s for s in rows if s.endswith("@sample.aaai.local")}

        todo = [
            tid for tid in sorted(answerable & set(v2_drafts))
            if f"ticket-{tid}@sample.aaai.local" not in present
        ]
        print(f"backfilling {len(todo)} tickets from stored eval drafts")

        n_ok = 0
        for tid in todo:
            row = samples[tid]
            pipeline = EmailPipeline()
            pipeline.drafter = StoredDrafter(v2_drafts[tid])
            email_data = {
                "from": f"ticket-{tid}@sample.aaai.local",
                "to": "workflowchairs@aaai.zendesk.com",
                "subject": row["subject"][:990],
                "body": row["question"],
                "timestamp": f"{row['month']}-01T00:00:00Z",
            }
            try:
                await pipeline.process_email(email_data, db)
                n_ok += 1
            except Exception as exc:  # noqa: BLE001 - keep backfilling
                print(f"  ticket {tid}: {type(exc).__name__}: {exc}", flush=True)
        print(f"done: {n_ok}/{len(todo)} backfilled")


if __name__ == "__main__":
    asyncio.run(main())
