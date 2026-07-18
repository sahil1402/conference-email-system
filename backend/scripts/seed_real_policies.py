"""Seed the REAL AAAI-27 policy corpus (93 chunks) into policy_documents.

Replaces the toy-KB seeding for deployments: loads
data/knowledge_base/policies.json (produced by chunk_policies.py) and
bulk-inserts it via PolicyRepository. Idempotent — skips when the table is
already populated. The FAISS retriever reads this table; run this before
starting the app with RETRIEVAL_BACKEND=faiss.

Run with:  cd backend && python scripts/seed_real_policies.py
"""

import asyncio
import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_DIR))

from app.db.database import async_session_factory  # noqa: E402
from app.repositories.policy_repository import PolicyRepository  # noqa: E402

_POLICIES_PATH = _ROOT_DIR / "data" / "knowledge_base" / "policies.json"


async def main() -> None:
    policies = json.loads(_POLICIES_PATH.read_text(encoding="utf-8"))
    repo = PolicyRepository()
    inserted = updated = 0
    async with async_session_factory() as db:
        for p in policies:
            outcome = await repo.upsert_by_key(db, p, source="aaai_scrape")
            inserted += outcome == "inserted"
            updated += outcome == "updated"
    print(f"Public layer synced from {_POLICIES_PATH.name}: {inserted} inserted, {updated} updated.")


if __name__ == "__main__":
    asyncio.run(main())
