"""Pipeline orchestrator (classify → retrieve → draft → route → persist).

Wires the four pipeline modules together and the persistence layer behind a
single entry point, `process_email`. It owns sequencing, timing, persistence,
and audit logging — the modules themselves stay unaware of each other and of
the database.

The router runs AFTER the drafter: the FAQ-vs-human_review lane is a property
of the generated draft's completeness/groundedness/self-rated confidence, not
of the classification alone (see `app.pipeline.router`).

Failure policy:
- A failure in classify / retrieve is fatal: it is logged and re-raised with
  status "error" (no partial record is persisted).
- A failed draft is non-fatal: the partial result is persisted with status
  "draft_failed" and returned (the drafter itself never raises). Routing then
  runs on that (possibly failed) draft.
"""

import logging
import time
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.chair_router import ChairAssignment, ChairInfo, get_chair_router
from app.core.config import settings
from app.core.tracing import PipelineTracer
from app.models.enums import EmailStatus
from app.pipeline.classifier import ClassificationResult, IntentClassifier
from app.pipeline.distiller import EmailDistiller
from app.pipeline.drafter import DraftResponse, ResponseDrafter
from app.pipeline.retriever import (
    RetrievedChunk,
    get_retriever,
    grounded_chunks_hash,
)
from app.pipeline.router import (
    LANE_HUMAN_REVIEW,
    EmailRouter,
    RoutingDecision,
    apply_self_sufficiency_floor,
)
from app.repositories.audit_repository import AuditRepository
from app.repositories.chair_repository import ChairRepository
from app.repositories.email_repository import EmailRepository

logger = logging.getLogger(__name__)

# Map a terminal pipeline status to the email's persisted lifecycle status.
_LIFECYCLE_STATUS = {
    "complete": EmailStatus.DRAFT_GENERATED.value,
    "draft_failed": EmailStatus.ROUTED.value,
}

# How many leading characters of the body to use as the retrieval query
# (legacy "prefix" strategy).
_RETRIEVAL_QUERY_CHARS = 300
# Fallback query when QUERY_STRATEGY == "distill" but distillation failed:
# subject + this much body (E003 arm B — best non-distilled formulation).
_FALLBACK_QUERY_BODY_CHARS = 600


class PipelineResult(BaseModel):
    """End-to-end result of processing one email through the pipeline."""

    email_id: str
    classification: ClassificationResult
    retrieved_chunks: list[RetrievedChunk]
    routing: RoutingDecision
    draft: DraftResponse
    processing_time_ms: float
    status: str  # "complete" | "draft_failed" | "error"


@dataclass
class _Computed:
    """Everything one pipeline run produces, before it is persisted.

    Lets ``process_email`` (create a new row) and ``reprocess_email`` (update an
    existing row in place) share the exact same classify→retrieve→draft→route
    compute and audit/trace finalization.
    """

    classification: ClassificationResult
    retrieved_chunks: list
    routing: RoutingDecision
    draft: DraftResponse
    chair_assignment: ChairAssignment | None
    status: str
    record: dict
    tracer: PipelineTracer
    start: float


class EmailPipeline:
    """Orchestrates the full classify → retrieve → draft → route → save flow."""

    def __init__(self) -> None:
        # Each module is instantiated from its swappable settings flag.
        self.classifier = IntentClassifier(strategy=settings.CLASSIFIER_BACKEND)
        self.retriever = get_retriever()
        self.router = EmailRouter(strategy=settings.ROUTING_STRATEGY)
        # The SECOND routing decision (Phase 6A): which chair a human_review
        # email goes to. Independent of the lane router above and swappable via
        # its own flag.
        self.chair_router = get_chair_router(settings.CHAIR_ROUTING_STRATEGY)
        # One model call producing retrieval queries + intent (E003). Only
        # consulted when QUERY_STRATEGY == "distill"; always best-effort.
        self.distiller = EmailDistiller()
        self.drafter = ResponseDrafter(provider=settings.MODEL_PROVIDER)
        self.email_repo = EmailRepository()
        self.chair_repo = ChairRepository()
        self.audit_repo = AuditRepository()

    async def _assign_chair(
        self,
        db: AsyncSession,
        classification: ClassificationResult,
        routing: RoutingDecision,
    ) -> ChairAssignment | None:
        """Pick a chair for a human-review email. Best-effort — never raises.

        Runs ONLY when the lane router chose human_review (FAQ-lane emails are
        auto-replied and never assigned to a chair). Fetches the active chair
        roster via the repository, projects it onto DB-free ``ChairInfo`` objects,
        and delegates the decision to the swappable strategy. A failure here (no
        chairs, DB hiccup) leaves the email unassigned rather than breaking the
        pipeline — chair assignment is additive to the core classify→retrieve→
        draft→route flow.
        """
        if routing.lane != LANE_HUMAN_REVIEW:
            return None
        try:
            chairs = await self.chair_repo.get_active_chairs(db)
            if not chairs:
                return None
            candidates = [
                ChairInfo(
                    id=c.id,
                    name=c.name,
                    role_title=c.role_title,
                    areas=list(c.areas or []),
                    active=c.active,
                )
                for c in chairs
            ]
            return self.chair_router.assign(classification, candidates)
        except Exception:  # noqa: BLE001 - assignment must not break the pipeline
            logger.warning("Chair assignment failed; leaving email unassigned.", exc_info=True)
            return None

    async def _compute(self, email_data: dict, db: AsyncSession) -> _Computed:
        """Run classify → retrieve → draft → route → chair-assign (no persistence).

        Shared by ``process_email`` (creates a new row) and ``reprocess_email``
        (updates an existing row). Builds the persistence ``record`` but does not
        write it; the caller decides create vs. update.
        """
        start = time.perf_counter()
        subject = email_data.get("subject", "")
        body = email_data.get("body", "")

        # Per-email tracer: buffers a record per stage and flushes them once the
        # email is persisted (its id is known then). Additive only.
        tracer = PipelineTracer()

        # --- classify → retrieve (fatal on failure) ------------------------
        try:
            with tracer.stage(
                "classifier",
                {"subject_length": len(subject), "body_length": len(body)},
            ) as st:
                # E003: one model call distills retrieval queries AND classifies
                # intent. Best-effort — on any distiller failure the keyword/
                # trainable backend classifies as before.
                distilled = None
                if settings.QUERY_STRATEGY == "distill":
                    distilled = await self.distiller.distill(subject, body)
                if distilled is not None and distilled.intent is not None:
                    classification = ClassificationResult(
                        intent=distilled.intent,
                        confidence=(
                            distilled.confidence
                            if distilled.confidence is not None
                            else 0.5
                        ),
                        reasoning="Distiller call (query distillation + intent).",
                        method="llm_distiller",
                    )
                else:
                    classification = await self.classifier.classify(body, subject)
                st.output_summary = {
                    "intent": classification.intent,
                    "confidence": round(float(classification.confidence), 4),
                    "method": classification.method,
                }
                # Surface both confidences in the trace when calibration is active.
                if classification.calibrated_confidence is not None:
                    st.output_summary["raw_confidence"] = round(
                        float(classification.raw_confidence), 4
                    )
                    st.output_summary["calibrated_confidence"] = round(
                        float(classification.calibrated_confidence), 4
                    )

            # Query formulation (E003): distilled queries joined into one string,
            # with NO intent token (it hurts dense retrieval — E001). Distill-mode
            # fallback is subject+body[:600]; the legacy prefix strategy keeps
            # body[:300] + intent token, bit-for-bit.
            if distilled is not None and distilled.queries:
                query = " ".join(distilled.queries)
                retrieval_intent = ""
            elif settings.QUERY_STRATEGY == "distill":
                query = f"{subject} {body[:_FALLBACK_QUERY_BODY_CHARS]}".strip()
                retrieval_intent = ""
            else:
                query = body[:_RETRIEVAL_QUERY_CHARS]
                retrieval_intent = classification.intent

            # Soft intent prior (B5): the classified intent feeds a fusion-only
            # score boost as METADATA on a SEPARATE channel from ``retrieval_intent``.
            # This is why it does NOT reintroduce E001 — ``query`` and
            # ``retrieval_intent`` (the args that shape the query text, "" in distill
            # mode) are unchanged; ``prior_intent`` never enters the query string.
            # Gated by INTENT_PRIOR_ENABLED (default False, B7): E010 showed this
            # boost badly regresses fusion retrieval (hit@1 .730→.243) as currently
            # sized, so production withholds the intent unless explicitly opted in
            # (docs/exp_tracking/E010_intent_prior.md).
            prior_intent = (
                (classification.intent or "") if settings.INTENT_PRIOR_ENABLED else ""
            )

            with tracer.stage("retriever", {"query": query}) as st:
                retrieved_chunks = await self.retriever.retrieve(
                    query,
                    retrieval_intent,
                    top_k=settings.MAX_RETRIEVED_CHUNKS,
                    prior_intent=prior_intent,
                )
                st.output_summary = {
                    "chunk_ids": [c.policy_id for c in retrieved_chunks],
                    "scores": [round(float(c.score), 4) for c in retrieved_chunks],
                    "backend": settings.RETRIEVAL_BACKEND,
                }

        except Exception:
            logger.exception("Pipeline failed before drafting; aborting.")
            raise

        # --- draft (non-fatal: failure downgrades status) -----------------
        with tracer.stage("drafter", {}) as st:
            draft = await self.drafter.draft(
                email_data, classification, retrieved_chunks
            )
            st.output_summary = {
                "draft_length": len(draft.draft_text),
                "provider": draft.generation_metadata.get("provider", self.drafter.provider),
                "model_used": draft.model_used,
                "placeholders": len(draft.placeholders),
                "answer_confidence": draft.answer_confidence,
            }
        status = "draft_failed" if draft.generation_metadata.get("error") else "complete"

        # --- route (now draft-aware): lane = FAQ iff the draft is self-sufficient.
        # The strategy-independent safety floor (chair placeholders or
        # notes-for-chair can NEVER be auto-answered) is applied INSIDE this stage,
        # before output_summary is set, so the persisted trace's lane always
        # matches the final lane (email.routing / audit log) — load-bearing for
        # the RL strategy, which routes without ever seeing the draft.
        with tracer.stage(
            "router",
            {
                "intent": classification.intent,
                "confidence": round(float(classification.confidence), 4),
            },
        ) as st:
            routing = self.router.route(classification, retrieved_chunks, draft)
            routing = apply_self_sufficiency_floor(routing, draft)
            st.output_summary = {
                "lane": routing.lane,
                "reason": routing.reason,
            }

        # --- chair assignment (the second routing decision) ---------------
        # Human-review only; returns None for FAQ-lane emails. Kept out of the
        # tracer stages (the trace contract is exactly classify→retrieve→draft→
        # route) and best-effort so it never breaks the core flow.
        chair_assignment = await self._assign_chair(db, classification, routing)

        record = {
            "sender": email_data.get("from") or email_data.get("sender") or "unknown@unknown",
            "sender_name": email_data.get("sender_name"),
            "subject": subject,
            "body": body,
            "status": _LIFECYCLE_STATUS[status],
            "classification": classification.model_dump(),
            "routing": routing.model_dump(),
            "draft": draft.model_dump(),
            "assigned_chair_id": (
                chair_assignment.chair_id if chair_assignment else None
            ),
            # Exact retriever inputs + the grounding set, so a later KB-change
            # sweep can re-run retrieval with no model call and compare. ``prior_intent``
            # is stored so re-eval reproduces the SAME boosted ranking from ctx alone
            # (legacy rows lack it → re-eval passes "" → their unboosted ids still
            # match). ``chunk_hash`` fingerprints (id, intents) so a KB intent re-label
            # that leaves the id-set unchanged is still detected (B5).
            "retrieval_context": {
                "query": query,
                "intent": retrieval_intent,
                "prior_intent": prior_intent,
                "retrieved_ids": [c.policy_id for c in retrieved_chunks],
                "chunk_hash": grounded_chunks_hash(retrieved_chunks),
            },
        }
        return _Computed(
            classification=classification,
            retrieved_chunks=retrieved_chunks,
            routing=routing,
            draft=draft,
            chair_assignment=chair_assignment,
            status=status,
            record=record,
            tracer=tracer,
            start=start,
        )

    async def _finalize(
        self, db: AsyncSession, email_id: str, c: _Computed
    ) -> PipelineResult:
        """Flush the per-stage trace + write audit rows for a persisted email."""
        # Now that the id exists, write the buffered per-stage trace records.
        c.tracer.flush(email_id)

        # --- audit each stage --------------------------------------------
        await self.audit_repo.log_action(
            db, email_id, "classified", "pipeline",
            {"intent": c.classification.intent, "confidence": c.classification.confidence},
        )
        await self.audit_repo.log_action(
            db, email_id, "retrieved", "pipeline",
            {"chunk_ids": [ch.policy_id for ch in c.retrieved_chunks]},
        )
        await self.audit_repo.log_action(
            db, email_id, "routed", "pipeline",
            {"lane": c.routing.lane, "override_reason": c.routing.override_reason},
        )
        # Record the chair assignment (human-review only) — captures the intent +
        # confidence at assignment time, which is exactly the signal a later
        # reroute compares against (Phase 6A step 3 / future training data).
        if c.chair_assignment and c.chair_assignment.chair_id is not None:
            await self.audit_repo.log_action(
                db, email_id, "chair_assigned", "pipeline",
                {
                    "chair_id": c.chair_assignment.chair_id,
                    "chair_name": c.chair_assignment.chair_name,
                    "intent": c.classification.intent,
                    "confidence": c.classification.confidence,
                    "is_fallback": c.chair_assignment.is_fallback,
                    "matched_area": c.chair_assignment.matched_area,
                    "strategy": c.chair_assignment.strategy,
                    "reason": c.chair_assignment.reason,
                },
            )
        await self.audit_repo.log_action(
            db, email_id, "drafted", "pipeline",
            {"model_used": c.draft.model_used, "status": c.status},
        )

        elapsed_ms = (time.perf_counter() - c.start) * 1000.0
        return PipelineResult(
            email_id=email_id,
            classification=c.classification,
            retrieved_chunks=c.retrieved_chunks,
            routing=c.routing,
            draft=c.draft,
            processing_time_ms=elapsed_ms,
            status=c.status,
        )

    async def process_email(
        self, email_data: dict, db: AsyncSession
    ) -> PipelineResult:
        """Run an email through the full pipeline and persist it as a NEW row."""
        c = await self._compute(email_data, db)
        email = await self.email_repo.create_email(db, c.record)
        return await self._finalize(db, str(email.id), c)

    async def reprocess_email(self, db: AsyncSession, email) -> PipelineResult:
        """Re-run the full pipeline for an EXISTING email and update it in place.

        Backs the per-email "retry" action: the same classify → retrieve → draft
        → route flow as ``process_email``, but the fresh outputs overwrite the
        existing row (its id, ``received_at`` and any chair-edit history stay put)
        instead of creating a new one. ``retrieval_context`` is refreshed too, and
        the transient ``redrafting`` flag is cleared as the new draft lands.
        """
        email_data = {
            "from": email.sender,
            "sender_name": email.sender_name,
            "subject": email.subject,
            "body": email.body,
        }
        c = await self._compute(email_data, db)
        await self.email_repo.update_email_outputs(db, str(email.id), c.record)
        return await self._finalize(db, str(email.id), c)
