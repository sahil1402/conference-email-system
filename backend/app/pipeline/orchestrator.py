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

from app.core.config import settings
from app.models.enums import EmailStatus
from app.pipeline.classifier import ClassificationResult, IntentClassifier
from app.pipeline.drafter import DraftResponse, ResponseDrafter
from app.pipeline.retriever import PolicyRetriever, RetrievedChunk
from app.pipeline.router import EmailRouter, RoutingDecision
from app.repositories.audit_repository import AuditRepository
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
        self.retriever = PolicyRetriever(backend=settings.RETRIEVAL_BACKEND)
        self.router = EmailRouter(strategy=settings.ROUTING_STRATEGY)
        self.drafter = ResponseDrafter(provider=settings.MODEL_PROVIDER)
        self.email_repo = EmailRepository()
        self.audit_repo = AuditRepository()

    async def process_email(
        self, email_data: dict, db: AsyncSession
    ) -> PipelineResult:
        """Run an email through the full pipeline and persist the result."""
        start = time.perf_counter()
        subject = email_data.get("subject", "")
        body = email_data.get("body", "")

        # --- classify → retrieve → route (fatal on failure) ---------------
        try:
            classification = await self.classifier.classify(body, subject)
            retrieved_chunks = await self.retriever.retrieve(
                body[:_RETRIEVAL_QUERY_CHARS],
                classification.intent,
                top_k=settings.MAX_RETRIEVED_CHUNKS,
            )
            routing = self.router.route(classification, retrieved_chunks)
        except Exception:
            logger.exception("Pipeline failed before drafting; aborting.")
            raise

        # --- draft (non-fatal: failure downgrades status) -----------------
        draft = await self.drafter.draft(
            email_data, classification, retrieved_chunks, routing
        )
        status = "draft_failed" if draft.generation_metadata.get("error") else "complete"

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
        }
        email = await self.email_repo.create_email(db, record)
        email_id = str(email.id)

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
