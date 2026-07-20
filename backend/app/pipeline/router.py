"""Router (the two-lane decision).

Decides whether an email is answered automatically (FAQ lane) or escalated to a
human chair (human-review lane). The FAQ lane is a property of the generated
*draft*, not the email's classified intent: auto-reply requires a COMPLETE
draft (no chair placeholders, no notes for the chair), GROUNDED in retrieved
policy (at least one citation), sufficient classifier confidence, AND
sufficient drafter self-rated answer confidence. Every condition must hold —
if any one fails, the email is escalated to a human with a specific reason.

The thresholds are read from settings (FAQ_CONFIDENCE_THRESHOLD,
FAQ_ANSWER_CONFIDENCE_THRESHOLD) so they are tunable without code changes. The
`strategy` flag is the seam for an RL router later.
"""

from pydantic import BaseModel, Field

from app.core.config import settings
from app.pipeline.classifier import ClassificationResult

# Intents that ALWAYS require a human regardless of draft quality. Kept as a
# seam (the check below still runs) but intentionally EMPTY: the FAQ lane is
# now decided by draft completeness, not intent, and appeals are answerable
# (a complete, grounded reply is auto-eligible). Re-populate to force-escalate
# specific intents. Vocabulary source: app.pipeline.taxonomy.
SENSITIVE_INTENTS: list[str] = []

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
        draft,
    ) -> RoutingDecision:
        """Pick a lane from the classification and the generated draft's quality."""
        threshold = settings.FAQ_CONFIDENCE_THRESHOLD
        answer_threshold = settings.FAQ_ANSWER_CONFIDENCE_THRESHOLD
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

        # RL strategy: delegate the lane choice to the learning bandit. It keeps
        # the same RoutingDecision contract and its own hard safety guards
        # (sensitive intents + a low-confidence floor). Imported lazily to avoid
        # a circular import (rl_router imports RoutingDecision from this module).
        if self.strategy == "rl":
            from app.pipeline.rl_router import get_rl_router

            return get_rl_router().route(intent, confidence, threshold)

        # Seam (empty by default) — force certain intents to a human if ever needed.
        if intent in SENSITIVE_INTENTS:
            return RoutingDecision(
                lane=LANE_HUMAN_REVIEW,
                reason=f"Routed to human review: '{intent}' is force-escalated.",
                confidence_used=confidence,
                threshold_applied=threshold,
                override_reason=f"Intent '{intent}' always requires human review",
            )

        # Draft-quality FAQ gate: every condition must hold.
        complete = not draft.placeholders and not draft.notes_for_chair
        grounded = bool(draft.citations)
        answer_conf = draft.answer_confidence
        if (
            complete
            and grounded
            and confidence >= threshold
            and answer_conf is not None
            and answer_conf >= answer_threshold
        ):
            return RoutingDecision(
                lane=LANE_FAQ,
                reason=(
                    f"Auto-reply eligible: complete grounded draft "
                    f"(answer_confidence {answer_conf:.2f} >= {answer_threshold:.2f}, "
                    f"intent confidence {confidence:.2f} >= {threshold:.2f})."
                ),
                confidence_used=confidence,
                threshold_applied=threshold,
            )

        if draft.placeholders:
            why = f"draft has {len(draft.placeholders)} chair placeholder(s)"
        elif draft.notes_for_chair:
            why = "draft has notes for the chair"
        elif not grounded:
            why = "draft cites no policy (ungrounded)"
        elif confidence < threshold:
            why = f"intent confidence {confidence:.2f} < {threshold:.2f}"
        elif answer_conf is None:
            why = "drafter provided no answer confidence"
        else:
            why = f"answer confidence {answer_conf:.2f} < {answer_threshold:.2f}"
        return RoutingDecision(
            lane=LANE_HUMAN_REVIEW,
            reason=f"Routed to human review: {why}.",
            confidence_used=confidence,
            threshold_applied=threshold,
        )
