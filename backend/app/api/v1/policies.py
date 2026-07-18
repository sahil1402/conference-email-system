"""KB governance API (v1) — chair authoring of the internal policy overlay.

Mounted under /api/v1 → paths are /api/v1/policies*. Every mutation writes a
policy_audit_logs entry and rebuilds the retriever index so the live KB reflects
the change with no restart.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.pipeline.retriever import get_retriever
from app.repositories.policy_audit_repository import PolicyAuditRepository
from app.repositories.policy_repository import PolicyRepository

router = APIRouter(prefix="/policies", tags=["policies"])

_policies = PolicyRepository()
_audit = PolicyAuditRepository()


class CreatePolicyRequest(BaseModel):
    title: str
    content: str
    category: str | None = None
    tags: list[str] | None = None
    actor: str
    retire_keys: list[str] = []


class RetireRequest(BaseModel):
    actor: str


class SimilarRequest(BaseModel):
    title: str
    content: str


async def _rebuild_index() -> None:
    """Clear the active retriever's cache so the next retrieve() reloads the KB.

    BM25's rebuild_index is sync (returns None); FAISS's and Fusion's are async
    (return a coroutine). Handle both without assuming which backend is active.
    """
    import inspect

    result = get_retriever().rebuild_index()
    if inspect.isawaitable(result):
        await result


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_policy(payload: CreatePolicyRequest, db: AsyncSession = Depends(get_db)) -> dict:
    row = await _policies.create_internal(
        db, title=payload.title, content=payload.content,
        category=payload.category, tags=payload.tags, actor=payload.actor,
    )
    await _audit.log(db, policy_key=row.policy_key, action="policy_created",
                     actor=f"chair:{payload.actor}", after={"title": row.title})
    for key in payload.retire_keys:
        existing = await _policies.get_by_key(db, key)
        if existing is None or existing.status == "inactive":
            continue
        prior = existing.status
        await _policies.retire(db, key)
        await _audit.log(db, policy_key=key, action="policy_retired",
                         actor=f"chair:{payload.actor}", before={"status": prior},
                         after={"status": "inactive", "superseded_by": row.policy_key})
    await _rebuild_index()
    return {"policy_key": row.policy_key, "visibility": row.visibility, "status": row.status}


@router.patch("/{policy_key}/retire")
async def retire_policy(policy_key: str, payload: RetireRequest, db: AsyncSession = Depends(get_db)) -> dict:
    row = await _policies.get_by_key(db, policy_key)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"policy {policy_key} not found")
    prior = row.status
    if prior == "inactive":
        return {"policy_key": policy_key, "status": "inactive"}
    await _policies.retire(db, policy_key)
    await _audit.log(db, policy_key=policy_key, action="policy_retired",
                     actor=f"chair:{payload.actor}", before={"status": prior},
                     after={"status": "inactive"})
    await _rebuild_index()
    return {"policy_key": policy_key, "status": "inactive"}


@router.post("/similar")
async def similar_policies(payload: SimilarRequest) -> dict:
    hits = await get_retriever().retrieve(f"{payload.title} {payload.content}", intent="", top_k=5)
    return {"similar": [{"policy_key": h.policy_id, "title": h.title, "score": h.score} for h in hits]}
