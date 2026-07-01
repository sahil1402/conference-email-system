"""Router (the two-lane decision).

Decides whether an email is answered automatically (FAQ lane) or escalated to a
human chair (human-review lane). The policy is deliberately conservative:
sensitive intents are always escalated, and FAQ auto-reply requires an eligible
intent, sufficient classifier confidence, AND retrieved grounding so the drafter
can never answer without policy text behind it.

The threshold is read from settings (FAQ_CONFIDENCE_THRESHOLD) so it is tunable
without code changes. The `strategy` flag is the seam for an RL router later.
"""

from pydantic import BaseModel, Field

from app.core.config import settings
from app.pipeline.classifier import ClassificationResult

# Intents that can be answered automatically when confidence + grounding allow.
FAQ_ELIGIBLE_INTENTS = [
    "submission_deadline",
    "formatting_requirements",
    "general_inquiry",
]

# Intents that ALWAYS require a human, regardless of confidence — these carry
# fairness, integrity, or interpersonal stakes an auto-reply must not touch.
SENSITIVE_INTENTS = [
    "authorship_dispute",
    "ethics_concern",
    "review_assignment",
]

LANE_FAQ = "faq"
LANE_HUMAN_REVIEW = "human_review"


class RoutingDecision(BaseModel):
    """Output of the router — the lane and a transparent rationale."""

    lane: str = Field(..., description='"faq" or "human_review".')
    reason: str = Field(..., description="Human-readable explanation of the lane.")
    confidence_used: float = Field(
        ..., description="Classifier confidence considered in the decision."
    )
    threshold_applied: float = Field(
        ..., description="FAQ confidence threshold that was applied."
    )
    override_reason: str | None = Field(
        default=None,
        description="Set when a hard rule forced the lane (e.g. sensitive intent).",
    )


class EmailRouter:
    """Threshold-and-rule router for the two-lane workflow."""

    def __init__(self, strategy: str = "threshold") -> None:
        self.strategy = strategy

    def route(
        self,
        classification: ClassificationResult,
        retrieved_chunks: list,
    ) -> RoutingDecision:
        """Pick a lane from the classification and the retrieved grounding."""
        threshold = settings.FAQ_CONFIDENCE_THRESHOLD
        intent = classification.intent
        # Prefer the calibrated confidence when the classifier attached one
        # (calibration enabled + a fitted artifact exists); otherwise use the
        # raw score. This changes only WHICH confidence value is compared — the
        # threshold logic below is untouched.
        confidence = (
            classification.calibrated_confidence
            if classification.calibrated_confidence is not None
            else classification.confidence
        )
        chunk_count = len(retrieved_chunks)

        # RL strategy: delegate the lane choice to the learning bandit. It keeps
        # the same RoutingDecision contract and its own hard safety guards
        # (sensitive intents + a low-confidence floor). Imported lazily to avoid
        # a circular import (rl_router imports RoutingDecision from this module).
        if self.strategy == "rl":
            from app.pipeline.rl_router import get_rl_router

            return get_rl_router().route(intent, confidence, threshold)

        # Hard rule: sensitive intents are never auto-answered.
        if intent in SENSITIVE_INTENTS:
            override = f"Intent '{intent}' always requires human review"
            return RoutingDecision(
                lane=LANE_HUMAN_REVIEW,
                reason=(
                    f"Routed to human review: '{intent}' is a sensitive intent "
                    f"that must be handled by a chair."
                ),
                confidence_used=confidence,
                threshold_applied=threshold,
                override_reason=override,
            )

        # FAQ auto-reply only when eligible, confident, AND grounded.
        if (
            intent in FAQ_ELIGIBLE_INTENTS
            and confidence >= threshold
            and chunk_count > 0
        ):
            return RoutingDecision(
                lane=LANE_FAQ,
                reason=(
                    f"Auto-reply eligible: intent '{intent}' with confidence "
                    f"{confidence:.2f} >= threshold {threshold:.2f} and "
                    f"{chunk_count} grounding chunk(s) retrieved."
                ),
                confidence_used=confidence,
                threshold_applied=threshold,
                override_reason=None,
            )

        # Everything else falls back to human review with a specific reason.
        if intent not in FAQ_ELIGIBLE_INTENTS:
            why = f"intent '{intent}' is not eligible for auto-reply"
        elif confidence < threshold:
            why = (
                f"confidence {confidence:.2f} is below threshold {threshold:.2f}"
            )
        else:
            why = "no grounding policy chunks were retrieved"

        return RoutingDecision(
            lane=LANE_HUMAN_REVIEW,
            reason=f"Routed to human review: {why}.",
            confidence_used=confidence,
            threshold_applied=threshold,
            override_reason=None,
        )
