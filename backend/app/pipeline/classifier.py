"""Intent classifier (Lane decision input #1).

Baseline keyword classifier — deliberately simple and dependency-free so it can
be swapped for a trainable model later without touching callers. It scores each
candidate intent by keyword overlap with the email's subject + body, picks the
top intent, and emits a calibrated confidence plus runner-up intents.

The `ClassificationResult` contract and the `classify` / `classify_batch`
signatures are what the router and orchestrator depend on; keep them stable.
"""

import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Backends we've already warned about (missing calibrator) — warn only once each.
_calibration_warned: set[str] = set()

from app.pipeline.taxonomy import VALID_INTENTS, FALLBACK_INTENT  # noqa: F401

# Keyword cues per intent, chosen for recall on the toy dataset's vocabulary.
# Lowercase; substring-matched against subject and body independently.
# The keyword path is a dormant fallback (production uses the distiller), so
# this ruleset is best-effort, not exhaustive.
KEYWORD_RULES: dict[str, list[str]] = {
    "reviewer_assignment": [
        "reviewer", "assign", "assignment", "emergency reviewer",
        "add a reviewer", "reassign",
    ],
    "review_submission_help": [
        "submit my review", "cannot submit review", "openreview down",
        "review site", "late review", "meta-review",
    ],
    "paper_bidding": ["bidding", "bid", "reviewer preference"],
    "author_profile_compliance": [
        "dblp", "semantic scholar", "google scholar", "profile",
        "subject area", "conflict",
    ],
    "submission_upload_help": [
        "upload", "resubmit", "restore", "withdrawn by mistake",
        "camera-ready file", "supplementary",
    ],
    "submission_requirements": [
        "deadline", "due date", "eligible", "how do i submit",
        "camera-ready deadline", "abstract deadline",
    ],
    "submission_format_policy": [
        "page limit", "appendix", "checklist", "format", "anonymized code",
        "template",
    ],
    "author_list_change": [
        "add author", "remove author", "author order", "co-author",
        "author list",
    ],
    "review_decision_appeal": [
        "appeal the decision", "unfair review", "rebuttal",
        "reconsider our paper", "review quality",
    ],
    "desk_reject_appeal": [
        "desk reject", "desk-reject", "appeal the desk",
        "rejected for formatting",
    ],
    "anonymity_violation": [
        "double blind", "double-blind", "anonymity", "de-anonymize",
        "identifying information",
    ],
    "reviewer_workload_role": [
        "too many papers", "reduce my load", "volunteer to review",
        "senior program committee", "area chair",
    ],
    "committee_invitation": [
        "invitation", "accept the invitation", "decline", "resend the link",
        "reactivate",
    ],
    "cms_support": [
        "account", "login", "cannot log in", "email address",
        "duplicate account",
    ],
}

# Multiplier applied to a keyword's score when it appears in BOTH subject and
# body (a strong signal the email is genuinely about that intent).
_BOTH_FIELDS_BOOST = 1.5
# Score is mapped to confidence as min(score / _CONFIDENCE_DIVISOR, _MAX_CONF).
_CONFIDENCE_DIVISOR = 5.0
_MAX_CONFIDENCE = 0.95
# Confidence penalty when the top two intents are nearly tied (ambiguous).
_TIE_MARGIN = 0.1
_TIE_PENALTY = 0.15


class ClassificationResult(BaseModel):
    """Output of the classifier — the intent decision and its confidence."""

    intent: str = Field(..., description="Best-guess intent from VALID_INTENTS.")
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence in the chosen intent."
    )
    reasoning: str = Field(
        default="", description="Human-readable rationale for the decision."
    )
    secondary_intents: list[str] = Field(
        default_factory=list,
        description="Up to 2 runner-up intents that also matched, score desc.",
    )
    method: str = Field(
        default="keyword",
        description='Backend that produced this result ("keyword" | "trained_classifier").',
    )
    # --- Calibration (Phase 5B; both None unless calibration is active) ---
    raw_confidence: float | None = Field(
        default=None,
        description="Original raw confidence, preserved when calibration is applied.",
    )
    calibrated_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Calibrated P(correct); set only when CALIBRATION_ENABLED and a "
        "fitted calibrator exists. The router prefers this when present.",
    )


def _score_intent(keywords: list[str], subject: str, body: str) -> float:
    """Sum keyword contributions, boosting cues found in both fields.

    ``subject`` and ``body`` must already be lowercased.
    """
    score = 0.0
    for kw in keywords:
        in_subject = kw in subject
        in_body = kw in body
        if not (in_subject or in_body):
            continue
        contribution = 1.0
        if in_subject and in_body:
            contribution *= _BOTH_FIELDS_BOOST
        score += contribution
    return score


def keyword_classify(subject: str, body: str) -> ClassificationResult:
    """Keyword-overlap intent classification (the baseline backend).

    Pure and synchronous so it can be reused directly by the trainable
    classifier's fallback path. ``IntentClassifier.classify`` wraps this.
    """
    subject_l = (subject or "").lower()
    body_l = (body or "").lower()

    scores: dict[str, float] = {
        intent: _score_intent(keywords, subject_l, body_l)
        for intent, keywords in KEYWORD_RULES.items()
    }

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_intent, top_score = ranked[0]

    # No keyword matched anywhere — fall back to a low-confidence inquiry.
    if top_score == 0:
        return ClassificationResult(
            intent=FALLBACK_INTENT,
            confidence=0.3,
            reasoning=f"No policy keywords matched; defaulting to {FALLBACK_INTENT}.",
            secondary_intents=[],
            method="keyword",
        )

    confidence = min(top_score / _CONFIDENCE_DIVISOR, _MAX_CONFIDENCE)

    # Penalise near-ties between the top two intents (ambiguous routing).
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    ambiguous = (top_score - second_score) <= _TIE_MARGIN and second_score > 0
    if ambiguous:
        confidence = max(0.0, confidence - _TIE_PENALTY)

    secondary = [intent for intent, score in ranked[1:] if score > 0][:2]

    reasoning = (
        f"Top intent '{top_intent}' scored {top_score:.2f} "
        f"(confidence {confidence:.2f})."
    )
    if ambiguous:
        reasoning += (
            f" Reduced by {_TIE_PENALTY} due to a near-tie with "
            f"'{ranked[1][0]}' ({second_score:.2f})."
        )

    return ClassificationResult(
        intent=top_intent,
        confidence=confidence,
        reasoning=reasoning,
        secondary_intents=secondary,
        method="keyword",
    )


class IntentClassifier:
    """Keyword-overlap intent classifier (baseline, swappable strategy)."""

    def __init__(self, strategy: str = "keyword") -> None:
        self.strategy = strategy

    async def classify(self, email_text: str, subject: str) -> ClassificationResult:
        """Classify a single email's intent from its body and subject.

        Dispatches on ``strategy``: ``trained``/``trainable`` delegates to the
        sentence-embedding model (which itself falls back to keyword scoring when
        no artifact is on disk); anything else uses keyword scoring directly. The
        public (async, ``email_text``-then-``subject``) signature is unchanged.
        """
        if self.strategy in ("trained", "trainable"):
            # Imported lazily so the heavy ML deps load only when this backend
            # is selected, keeping the keyword path import-light.
            from app.pipeline.trainable_classifier import get_trainable_classifier

            result = get_trainable_classifier().classify(subject, email_text)
        else:
            result = keyword_classify(subject, email_text)

        return self._apply_calibration(result)

    def _apply_calibration(self, result: ClassificationResult) -> ClassificationResult:
        """Attach calibrated confidence when calibration is enabled + available.

        Side-effect-free on the raw score: ``result.confidence`` is left as the
        raw classifier output; only the separate ``raw_confidence`` /
        ``calibrated_confidence`` fields are populated. Never raises — a missing
        artifact logs a warning once and falls back to raw confidence.
        """
        # Imported lazily so config/settings are read fresh and to avoid a
        # classifier ↔ calibration import cycle.
        from app.core.config import settings

        if not settings.CALIBRATION_ENABLED:
            return result

        from app.pipeline.calibration import backend_key, get_calibrator

        key = backend_key(self.strategy)
        calibrator = get_calibrator(key)
        if calibrator is None:
            if key not in _calibration_warned:
                _calibration_warned.add(key)
                logger.warning(
                    "CALIBRATION_ENABLED is set but no fitted calibrator exists "
                    "for backend '%s'; falling back to raw confidence.",
                    key,
                )
            return result

        result.raw_confidence = result.confidence
        result.calibrated_confidence = calibrator.calibrate(result.confidence)
        return result

    async def classify_batch(
        self, emails: list[dict]
    ) -> list[ClassificationResult]:
        """Classify many emails, preserving input order.

        Each dict is expected to carry ``body`` and ``subject`` keys.
        """
        results: list[ClassificationResult] = []
        for email in emails:
            results.append(
                await self.classify(
                    email.get("body", ""), email.get("subject", "")
                )
            )
        return results
