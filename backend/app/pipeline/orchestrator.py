"""Pipeline orchestrator (classify → retrieve → route → draft → persist).

Wires the four pipeline modules together and the persistence layer behind a
single entry point, `process_email`. It owns sequencing, timing, persistence,
and audit logging — the modules themselves stay unaware of each other and of
the database.

Failure policy:
- A failure in classify / retrieve / route is fatal: it is logged and re-raised
  with status "error" (no partial record is persisted).
- A failed draft is non-fatal: the partial result is persisted with status
  "draft_failed" and returned (the drafter itself never raises).
"""

import logging
import time

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.chair_router import ChairAssignment, ChairInfo, get_chair_router
from app.core.config import settings
from app.core.tracing import PipelineTracer
from app.models.enums import EmailStatus
from app.pipeline.classifier import ClassificationResult, IntentClassifier
from app.pipeline.drafter import DraftResponse, ResponseDrafter
from app.pipeline.retriever import RetrievedChunk, get_retriever
from app.pipeline.router import LANE_HUMAN_REVIEW, EmailRouter, RoutingDecision
from app.repositories.audit_repository import AuditRepository
from app.repositories.chair_repository import ChairRepository
from app.repositories.email_repository import EmailRepository

logger = logging.getLogger(__name__)

# Map a terminal pipeline status to the email's persisted lifecycle status.
_LIFECYCLE_STATUS = {
    "complete": EmailStatus.DRAFT_GENERATED.value,
    "draft_failed": EmailStatus.ROUTED.value,
}

# How many leading characters of the body to use as the retrieval query.
_RETRIEVAL_QUERY_CHARS = 300


class PipelineResult(BaseModel):
    """End-to-end result of processing one email through the pipeline."""

    email_id: str
    classification: ClassificationResult
    retrieved_chunks: list[RetrievedChunk]
    routing: RoutingDecision
    draft: DraftResponse
    processing_time_ms: float
    status: str  # "complete" | "draft_failed" | "error"


class EmailPipeline:
    """Orchestrates the full classify → retrieve → route → draft → save flow."""

    def __init__(self) -> None:
        # Each module is instantiated from its swappable settings flag.
        self.classifier = IntentClassifier(strategy=settings.CLASSIFIER_BACKEND)
        self.retriever = get_retriever()
        self.router = EmailRouter(strategy=settings.ROUTING_STRATEGY)
        # The SECOND routing decision (Phase 6A): which chair a human_review
        # email goes to. Independent of the lane router above and swappable via
        # its own flag.
        self.chair_router = get_chair_router(settings.CHAIR_ROUTING_STRATEGY)
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
        pipeline — chair assignment is additive to the core classify→route→draft
        flow.
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

    async def process_email(
        self, email_data: dict, db: AsyncSession
    ) -> PipelineResult:
        """Run an email through the full pipeline and persist the result."""
        start = time.perf_counter()
        subject = email_data.get("subject", "")
        body = email_data.get("body", "")

        # Per-email tracer: buffers a record per stage and flushes them once the
        # email is persisted (its id is unknown until then). Additive only — it
        # never alters stage inputs/outputs.
        tracer = PipelineTracer()
        query = body[:_RETRIEVAL_QUERY_CHARS]

        # --- classify → retrieve → route (fatal on failure) ---------------
        try:
            with tracer.stage(
                "classifier",
                {"subject_length": len(subject), "body_length": len(body)},
            ) as st:
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

            with tracer.stage("retriever", {"query": query}) as st:
                retrieved_chunks = await self.retriever.retrieve(
                    query,
                    classification.intent,
                    top_k=settings.MAX_RETRIEVED_CHUNKS,
                )
                st.output_summary = {
                    "chunk_ids": [c.policy_id for c in retrieved_chunks],
                    "scores": [round(float(c.score), 4) for c in retrieved_chunks],
                    "backend": settings.RETRIEVAL_BACKEND,
                }

            with tracer.stage(
                "router",
                {
                    "confidence": round(float(classification.confidence), 4),
                    "intent": classification.intent,
                },
            ) as st:
                routing = self.router.route(classification, retrieved_chunks)
                st.output_summary = {
                    "lane": routing.lane,
                    "reason": routing.reason,
                }
        except Exception:
            logger.exception("Pipeline failed before drafting; aborting.")
            raise

        # --- draft (non-fatal: failure downgrades status) -----------------
        with tracer.stage("drafter", {"lane": routing.lane}) as st:
            draft = await self.drafter.draft(
                email_data, classification, retrieved_chunks, routing
            )
            st.output_summary = {
                "draft_length": len(draft.draft_text),
                "provider": draft.generation_metadata.get("provider", self.drafter.provider),
                "model_used": draft.model_used,
            }
        status = "draft_failed" if draft.generation_metadata.get("error") else "complete"

        # --- chair assignment (the second routing decision) ---------------
        # Human-review only; returns None for FAQ-lane emails. Kept out of the
        # tracer stages (the trace contract is exactly classify→retrieve→route→
        # draft) and best-effort so it never breaks the core flow.
        chair_assignment = await self._assign_chair(db, classification, routing)

        # --- persist the email with its pipeline outputs ------------------
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
        }
        email = await self.email_repo.create_email(db, record)
        email_id = str(email.id)

        # Now that the id exists, write the buffered per-stage trace records.
        tracer.flush(email_id)

        # --- audit each stage --------------------------------------------
        await self.audit_repo.log_action(
            db, email_id, "classified", "pipeline",
            {"intent": classification.intent, "confidence": classification.confidence},
        )
        await self.audit_repo.log_action(
            db, email_id, "retrieved", "pipeline",
            {"chunk_ids": [c.policy_id for c in retrieved_chunks]},
        )
        await self.audit_repo.log_action(
            db, email_id, "routed", "pipeline",
            {"lane": routing.lane, "override_reason": routing.override_reason},
        )
        # Record the chair assignment (human-review only) — captures the intent +
        # confidence at assignment time, which is exactly the signal a later
        # reroute compares against (Phase 6A step 3 / future training data).
        if chair_assignment and chair_assignment.chair_id is not None:
            await self.audit_repo.log_action(
                db, email_id, "chair_assigned", "pipeline",
                {
                    "chair_id": chair_assignment.chair_id,
                    "chair_name": chair_assignment.chair_name,
                    "intent": classification.intent,
                    "confidence": classification.confidence,
                    "is_fallback": chair_assignment.is_fallback,
                    "matched_area": chair_assignment.matched_area,
                    "strategy": chair_assignment.strategy,
                    "reason": chair_assignment.reason,
                },
            )
        await self.audit_repo.log_action(
            db, email_id, "drafted", "pipeline",
            {"model_used": draft.model_used, "status": status},
        )

        elapsed_ms = (time.perf_counter() - start) * 1000.0

        return PipelineResult(
            email_id=email_id,
            classification=classification,
            retrieved_chunks=retrieved_chunks,
            routing=routing,
            draft=draft,
            processing_time_ms=elapsed_ms,
            status=status,
        )
