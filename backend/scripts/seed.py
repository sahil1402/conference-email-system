"""Seed the database from the toy dataset and knowledge base.

Idempotent for policies (skips insert when the table is already populated).
Runs every toy email through the full pipeline so the queue, detail, and
analytics endpoints have realistic data to serve.

Run with:  cd backend && python scripts/seed.py
"""

import asyncio
import json
import sys
from pathlib import Path

# scripts/seed.py → parents[1] is backend/ (put it on sys.path so `app`
# imports work when run as `python scripts/seed.py`), parents[2] is repo root.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_DIR))

from app.db.database import async_session_factory  # noqa: E402
from app.pipeline.orchestrator import EmailPipeline  # noqa: E402
from app.repositories.policy_repository import PolicyRepository  # noqa: E402

_POLICIES_PATH = _ROOT_DIR / "data" / "knowledge_base" / "policies.json"
_EMAILS_PATH = _ROOT_DIR / "data" / "emails" / "toy_dataset.json"


def _load_json(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


async def main() -> None:
    policies = _load_json(_POLICIES_PATH)
    emails = _load_json(_EMAILS_PATH)

    policy_repo = PolicyRepository()
    pipeline = EmailPipeline()

    async with async_session_factory() as db:
        # --- policies (idempotent) ------------------------------------
        existing = await policy_repo.get_all_policies(db)
        if existing:
            print(f"Policies already present ({len(existing)}); skipping insert.")
        else:
            inserted = await policy_repo.bulk_insert_policies(db, policies)
            print(f"Inserted {inserted} policies.")

        # --- emails through the pipeline ------------------------------
        total = len(emails)
        processed = 0
        faq = 0
        human_review = 0
        failures = 0
        confidences: list[float] = []

        for index, raw in enumerate(emails, start=1):
            subject = raw.get("subject", "")
            print(f"Processing [{index}/{total}]: {subject}")
            email_data = {
                "from": raw.get("from"),
                "to": raw.get("to"),
                "subject": subject,
                "body": raw.get("body", ""),
                "timestamp": raw.get("timestamp", ""),
            }
            try:
                result = await pipeline.process_email(email_data, db)
            except Exception as exc:  # noqa: BLE001 - keep seeding on failure
                failures += 1
                print(f"  ERROR processing email {index}: {exc}")
                continue

            processed += 1
            confidences.append(result.classification.confidence)
            if result.routing.lane == "faq":
                faq += 1
            elif result.routing.lane == "human_review":
                human_review += 1

        avg_confidence = (
            sum(confidences) / len(confidences) if confidences else 0.0
        )

        print("\n=== Seed summary ===")
        print(f"Total processed: {processed}/{total}")
        print(f"FAQ lane:        {faq}")
        print(f"Human review:    {human_review}")
        print(f"Avg confidence:  {avg_confidence:.3f}")
        print(f"Failures:        {failures}")


if __name__ == "__main__":
    asyncio.run(main())
