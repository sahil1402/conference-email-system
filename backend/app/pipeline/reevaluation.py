"""Re-evaluate open tickets after a KB change (the chair's "re-evaluate" button).

One sweep re-runs retrieval for every open ticket using the query captured at
ingest (no model call) and re-drafts only the tickets whose grounding set moved.
Chair-edited drafts are never clobbered. The sweep runs in its own DB session so
it can be scheduled as a FastAPI background task after the request returns.

Gate (design §3): a ticket is *affected* iff the set of fresh top-k policy ids
differs from the set stored at draft time. Comparing sets (not ordered lists)
means a pure score reshuffle — same chunks, different order — does not force a
needless re-draft.
"""

import logging

from app.core.config import settings
from app.db.database import async_session_factory
from app.pipeline.classifier import ClassificationResult
from app.pipeline.drafter import ResponseDrafter
from app.pipeline.retriever import get_retriever
from app.pipeline.router import LANE_HUMAN_REVIEW, EmailRouter
from app.repositories.audit_repository import AuditRepository
from app.repositories.email_repository import EmailRepository

logger = logging.getLogger(__name__)

_ACTOR = "reevaluation"


async def _fresh_topk_ids(retriever, ctx: dict, top_k: int):
    """Re-run retrieval from a ticket's stored context; return (ids, chunks)."""
    query = (ctx or {}).get("query") or ""
    intent = (ctx or {}).get("intent") or ""
    chunks = await retriever.retrieve(query, intent, top_k=top_k)
    return [c.policy_id for c in chunks], chunks


async def reevaluate_open_tickets(session_factory=async_session_factory) -> dict:
    """Sweep open tickets; re-draft the ones whose retrieval changed.

    Returns a summary: {"open", "redrafted", "skipped_edited", "unaffected"}.
    Best-effort per ticket — a failure on one ticket is logged, its ``redrafting``
    flag cleared, and the sweep continues.
    """
    email_repo = EmailRepository()
    audit_repo = AuditRepository()
    retriever = get_retriever()
    router = EmailRouter(strategy=settings.ROUTING_STRATEGY)
    drafter = ResponseDrafter(provider=settings.MODEL_PROVIDER)
    top_k = settings.MAX_RETRIEVED_CHUNKS

    stats = {"open": 0, "redrafted": 0, "skipped_edited": 0, "unaffected": 0}

    async with session_factory() as db:
        tickets = await email_repo.get_open_tickets(db)
        stats["open"] = len(tickets)

        for email in tickets:
            # A ticket already mid-redraft (a prior in-flight sweep) is left alone.
            if email.redrafting:
                continue
            ctx = email.retrieval_context or {}
            stored_ids = set(ctx.get("retrieved_ids") or [])

            fresh_ids_list, fresh_chunks = await _fresh_topk_ids(retriever, ctx, top_k)
            if set(fresh_ids_list) == stored_ids:
                stats["unaffected"] += 1
                continue

            email_id = str(email.id)

            # Affected but chair-edited → never clobber; audit that it *would*
            # have changed so the chair knows their edit was preserved.
            if (email.draft or {}).get("is_edited"):
                await audit_repo.log_action(
                    db, email_id, "ticket_redraft_skipped_edited", _ACTOR,
                    {"stored_ids": sorted(stored_ids), "fresh_ids": fresh_ids_list},
                )
                stats["skipped_edited"] += 1
                continue

            try:
                await email_repo.set_redrafting(db, email_id, True)
                await audit_repo.log_action(
                    db, email_id, "ticket_redrafting", _ACTOR,
                    {"stored_ids": sorted(stored_ids), "fresh_ids": fresh_ids_list},
                )

                classification = ClassificationResult(**(email.classification or {}))
                email_data = {
                    "from": email.sender,
                    "sender_name": email.sender_name,
                    "subject": email.subject,
                    "body": email.body,
                }

                routing = router.route(classification, fresh_chunks)
                draft = await drafter.draft(
                    email_data, classification, fresh_chunks, routing
                )
                # Same placeholder→human_review rule the orchestrator applies:
                # a draft with [CHAIR: …] gaps always needs a human.
                if draft.placeholders and routing.lane != LANE_HUMAN_REVIEW:
                    routing = routing.model_copy(
                        update={
                            "lane": LANE_HUMAN_REVIEW,
                            "override_reason": (
                                f"draft contains {len(draft.placeholders)} chair "
                                "placeholder(s) requiring input before sending"
                            ),
                        }
                    )

                before_ph = len((email.draft or {}).get("placeholders") or [])
                new_ctx = {
                    "query": ctx.get("query", ""),
                    "intent": ctx.get("intent", ""),
                    "retrieved_ids": fresh_ids_list,
                }
                await email_repo.save_redraft(
                    db, email_id,
                    draft=draft.model_dump(),
                    routing=routing.model_dump(),
                    retrieval_context=new_ctx,
                )
                await audit_repo.log_action(
                    db, email_id, "ticket_redrafted", _ACTOR,
                    {
                        "stored_ids": sorted(stored_ids),
                        "fresh_ids": fresh_ids_list,
                        "placeholders_before": before_ph,
                        "placeholders_after": len(draft.placeholders),
                        "lane": routing.lane,
                    },
                )
                stats["redrafted"] += 1
            except Exception:  # noqa: BLE001 - one bad ticket must not stop the sweep
                logger.exception("Re-draft failed for email %s; clearing flag.", email_id)
                await email_repo.set_redrafting(db, email_id, False)

    logger.info("Re-evaluation sweep complete: %s", stats)
    return stats
