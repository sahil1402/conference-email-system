"""Policies API (v1) — read-only single-policy lookup for citation detail.

Mounted under ``/api/v1``, so the public path is ``/api/v1/policies/{policy_key}``.
Backs the citation-detail popup in the review UI: given a cited policy id (e.g.
``policy_117``), it returns the full chunk — source document, tags, and body
text — which the persisted email row does NOT carry (retrieved_chunks are not
stored on the email; only the cited ids survive in the draft).

READ-ONLY (Piece 3 — citation detail). Piece 4 (policy CRUD) is expected to
EXTEND THIS ROUTER with POST / PATCH / DELETE on the same ``/policies`` resource
— keep those additions here rather than starting a new module.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.repositories.policy_repository import PolicyRepository

router = APIRouter(prefix="/policies", tags=["policies"])

policy_repo = PolicyRepository()


class PolicyDetail(BaseModel):
    """Full policy chunk returned for the citation-detail popup (read-only)."""

    model_config = ConfigDict(from_attributes=True)

    policy_key: str = Field(..., description="Knowledge-base id of the chunk.")
    title: str = Field(..., description="Chunk title.")
    content: str = Field(..., description="Full chunk body text.")
    category: str | None = Field(default=None, description="Policy category.")
    tags: list[str] = Field(default_factory=list, description="Taxonomy tags.")
    source: str | None = Field(default=None, description="Origin document name.")
    score: float | None = Field(default=None, description="Curation score, if any.")


@router.get("/{policy_key}", response_model=PolicyDetail)
async def get_policy(
    policy_key: str, db: AsyncSession = Depends(get_db)
) -> PolicyDetail:
    """Return one policy chunk by its ``policy_key`` (e.g. ``policy_117``).

    404 if no chunk carries that key. Read-only citation lookup for the review
    UI; see the module docstring re: Piece 4 CRUD extending this router.
    """
    policy = await policy_repo.get_by_key(db, policy_key)
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No policy with key '{policy_key}'.",
        )
    return PolicyDetail(
        policy_key=policy.policy_key,
        title=policy.title,
        content=policy.content,
        category=policy.category,
        tags=list(policy.tags or []),
        source=policy.source,
        score=policy.score,
    )
