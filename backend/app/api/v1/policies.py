"""Policies API (v1) — citation-detail lookup + chair governance of the KB.

Mounted under ``/api/v1`` → paths are ``/api/v1/policies*``.

- Read: ``GET /policies`` (filtered list), ``GET /policies/audit`` (governance
  history), and ``GET /policies/{policy_key}`` (the full chunk for the
  citation-detail popup — source, tags, body — which the persisted email row does
  not carry).
- Governance (chair authoring of the internal overlay): ``POST /policies``
  (create internal, optionally superseding via ``retire_keys``),
  ``PATCH /policies/{key}/retire`` / ``/reactivate``, and ``POST /policies/similar``
  (the override similarity-assist). Every mutation writes a ``policy_audit_logs``
  entry and rebuilds the retriever index so the live KB reflects the change with
  no restart.

Route order matters: the static ``GET ""`` and ``GET /audit`` are declared BEFORE
``GET /{policy_key}`` so ``/policies/audit`` is not captured as a path param.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.pipeline.retriever import get_retriever
from app.repositories.policy_audit_repository import PolicyAuditRepository
from app.repositories.policy_repository import PolicyRepository
from app.pipeline.reevaluation import reevaluate_open_tickets
from app.repositories.email_repository import EmailRepository

router = APIRouter(prefix="/policies", tags=["policies"])

_policies = PolicyRepository()
_audit = PolicyAuditRepository()
_emails = EmailRepository()


class PolicyDetail(BaseModel):
    """Full policy chunk returned for the citation-detail popup (read-only)."""

    model_config = ConfigDict(from_attributes=True)

    policy_key: str = Field(..., description="Knowledge-base id of the chunk.")
    title: str = Field(..., description="Chunk title.")
    content: str = Field(..., description="Full chunk body text.")
    category: str | None = Field(default=None, description="Policy category.")
    # [tags-dropped E007] tags: list[str] = Field(default_factory=list, description="Taxonomy tags.")
    source: str | None = Field(default=None, description="Origin document name.")
    score: float | None = Field(default=None, description="Curation score, if any.")


class CreatePolicyRequest(BaseModel):
    title: str
    content: str
    category: str | None = None
    # [tags-dropped E007] tags: list[str] | None = None
    actor: str
    retire_keys: list[str] = []


class RetireRequest(BaseModel):
    actor: str


class SimilarRequest(BaseModel):
    title: str
    content: str


class ReactivateRequest(BaseModel):
    actor: str


class EditPolicyRequest(BaseModel):
    title: str
    content: str
    category: str | None = None
    # None ⇒ preserve the base policy's current visibility.
    visibility: str | None = None
    actor: str
    # ISO string the client last saw; None ⇒ skip the optimistic-concurrency
    # check (used by the injection similar-list, which edits a freshly-read hit).
    expected_updated_at: str | None = None


class RevertEditRequest(BaseModel):
    actor: str


async def _rebuild_index() -> None:
    """Clear the active retriever's cache so the next retrieve() reloads the KB.

    BM25's rebuild_index is sync (returns None); FAISS's and Fusion's are async
    (return a coroutine). Handle both without assuming which backend is active.
    """
    import inspect

    result = get_retriever().rebuild_index()
    if inspect.isawaitable(result):
        await result


def _policy_dict(p) -> dict:
    return {
        "policy_key": p.policy_key, "title": p.title, "content": p.content,
        # [tags-dropped E007] "tags": p.tags or [],
        "category": p.category, "visibility": p.visibility,
        "status": p.status, "source": p.source,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "supersedes": p.supersedes, "superseded_by": p.superseded_by,
        "root_key": p.root_key, "version": p.version,
    }


@router.get("")
async def list_policies(
    visibility: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> dict:
    rows = await _policies.list(
        db, visibility=visibility, status=status, search=search, limit=limit, offset=offset
    )
    return {"policies": [_policy_dict(p) for p in rows]}


@router.get("/audit")
async def list_policy_audit(
    limit: int = 200, offset: int = 0, db: AsyncSession = Depends(get_db)
) -> dict:
    rows = await _audit.list(db, limit=limit, offset=offset)
    return {"entries": [
        {"id": e.id, "policy_key": e.policy_key, "action": e.action, "actor": e.actor,
         "before": e.before, "after": e.after,
         "timestamp": e.timestamp.isoformat() if e.timestamp else None}
        for e in rows
    ]}


@router.post("/reevaluate", status_code=status.HTTP_202_ACCEPTED)
async def reevaluate(
    background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)
) -> dict:
    """Schedule one background re-draft sweep of the open tickets.

    The chair clicks this after making a batch of KB edits. We report how many
    open tickets exist (so the UI can say "sweeping N…") and run the sweep after
    the response returns — it re-drafts only the tickets whose retrieval shifted.
    The sweep opens its own DB session (the request's session is closed by then).
    """
    open_count = len(await _emails.get_open_tickets(db))
    background_tasks.add_task(reevaluate_open_tickets)
    return {"open": open_count, "scheduled": True}


@router.get("/{policy_key}", response_model=PolicyDetail)
async def get_policy(
    policy_key: str, db: AsyncSession = Depends(get_db)
) -> PolicyDetail:
    """Return one policy chunk by its ``policy_key`` (e.g. ``policy_117``).

    404 if no chunk carries that key. Read-only citation lookup for the review UI.
    Declared AFTER the static GET routes above so ``/policies/audit`` is not
    captured here as a path param.
    """
    policy = await _policies.get_by_key(db, policy_key)
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
        # [tags-dropped E007] tags=list(policy.tags or []),
        source=policy.source,
        score=policy.score,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_policy(payload: CreatePolicyRequest, db: AsyncSession = Depends(get_db)) -> dict:
    row = await _policies.create_internal(
        db, title=payload.title, content=payload.content,
        # [tags-dropped E007] tags=payload.tags,
        category=payload.category, actor=payload.actor,
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


@router.patch("/{policy_key}/reactivate")
async def reactivate_policy(
    policy_key: str, payload: ReactivateRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    existing = await _policies.get_by_key(db, policy_key)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"policy {policy_key} not found")
    if existing.status == "active":  # no-op, don't audit/rebuild
        return {"policy_key": policy_key, "status": "active"}
    row = await _policies.reactivate(db, policy_key)
    await _audit.log(db, policy_key=policy_key, action="policy_reactivated",
                     actor=f"chair:{payload.actor}", before={"status": "inactive"},
                     after={"status": "active"})
    await _rebuild_index()
    return {"policy_key": policy_key, "status": row.status}


@router.patch("/{policy_key}/edit")
async def edit_policy(
    policy_key: str, payload: EditPolicyRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Edit an active policy → a new active version; retire the base.

    Only the active tip of a lineage is editable. Optimistic concurrency: if the
    client passes ``expected_updated_at`` and it no longer matches, the edit is
    rejected (409) so a stale form cannot clobber a newer version.
    """
    base = await _policies.get_by_key(db, policy_key)
    if base is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"policy {policy_key} not found")
    if base.status != "active" or base.superseded_by is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Only an active, current policy can be edited."},
        )
    if payload.expected_updated_at is not None:
        current = base.updated_at.isoformat() if base.updated_at else None
        if current != payload.expected_updated_at:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "Policy changed since you loaded it; reload and retry.",
                        "current_updated_at": current},
            )
    visibility = payload.visibility or base.visibility
    before = {"policy_key": base.policy_key, "title": base.title,
              "content": base.content, "visibility": base.visibility, "status": base.status}
    new_row = await _policies.edit_policy(
        db, base=base, title=payload.title, content=payload.content,
        category=payload.category, visibility=visibility, actor=payload.actor,
    )
    await _audit.log(
        db, policy_key=new_row.policy_key, action="policy_edited",
        actor=f"chair:{payload.actor}", before=before,
        after={"policy_key": new_row.policy_key, "title": new_row.title,
               "content": new_row.content, "visibility": new_row.visibility,
               "status": new_row.status, "supersedes": new_row.supersedes,
               "version": new_row.version},
    )
    await _rebuild_index()
    return _policy_dict(new_row)


@router.post("/{policy_key}/revert-edit")
async def revert_edit(
    policy_key: str, payload: RevertEditRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Undo one edit: reactivate the immediately-prior version, retire this tip.

    Repeatable — after a revert the ancestor is the tip again. Only a current
    (active, not-yet-superseded) edited tip with a ``supersedes`` ancestor can be
    reverted.
    """
    tip = await _policies.get_by_key(db, policy_key)
    if tip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"policy {policy_key} not found")
    if tip.status != "active" or tip.superseded_by is not None or not tip.supersedes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Only a current edited policy with a prior version can be reverted."},
        )
    ancestor = await _policies.get_by_key(db, tip.supersedes)
    if ancestor is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "Prior version no longer exists; cannot revert."},
        )
    before = {"policy_key": tip.policy_key, "title": tip.title,
              "content": tip.content, "status": tip.status}
    restored = await _policies.revert_edit(db, tip=tip)
    await _audit.log(
        db, policy_key=tip.policy_key, action="policy_edit_reverted",
        actor=f"chair:{payload.actor}", before=before,
        after={"policy_key": restored.policy_key, "title": restored.title,
               "content": restored.content, "status": restored.status},
    )
    await _rebuild_index()
    return _policy_dict(restored)


@router.post("/similar")
async def similar_policies(payload: SimilarRequest) -> dict:
    hits = await get_retriever().retrieve(f"{payload.title} {payload.content}", intent="", top_k=5)
    return {
        "similar": [
            {"policy_key": h.policy_id, "title": h.title, "score": h.score, "content": h.content}
            for h in hits
        ]
    }
