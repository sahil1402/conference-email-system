"""Intent classifier (Lane decision input #1).

Baseline keyword classifier — deliberately simple and dependency-free so it can
be swapped for a trainable model later without touching callers. It scores each
candidate intent by keyword overlap with the email's subject + body, picks the
top intent, and emits a calibrated confidence plus runner-up intents.

The `ClassificationResult` contract and the `classify` / `classify_batch`
signatures are what the router and orchestrator depend on; keep them stable.
"""

from pydantic import BaseModel, Field

VALID_INTENTS = [
    "submission_deadline",
    "formatting_requirements",
    "general_inquiry",
    "review_assignment",
    "authorship_dispute",
    "submission_withdrawal",
    "ethics_concern",
    "technical_issue",
]

# Keyword cues per intent, chosen for recall on the toy dataset's vocabulary.
# Lowercase; substring-matched against subject and body independently.
KEYWORD_RULES: dict[str, list[str]] = {
    "submission_deadline": [
        "deadline", "due date", "submission date", "extension", "aoe",
        "anywhere on earth", "abstract deadline", "camera-ready", "cutoff",
        "when is", "timezone", "midnight", "notification date",
    ],
    "formatting_requirements": [
        "page limit", "template", "latex", "format", "formatting", "font",
        "two-column", "anonymiz", "double-blind", "supplementary", "margins",
        "style file", "references", "appendix", "word limit",
    ],
    "general_inquiry": [
        "registration", "fee", "virtual", "attend", "workshop", "first time",
        "how many", "proceedings", "present", "co-author", "inquiry",
    ],
    "review_assignment": [
        "review", "reviewer", "assigned", "assignment", "reassign",
        "conflict of interest", "decline", "review load", "papers to review",
        "area chair", "cannot access", "reviewer account",
    ],
    "authorship_dispute": [
        "author order", "authorship", "co-author added", "without my consent",
        "without consent", "contribution", "credit", "author list",
        "remove me", "added as", "author dispute", "missing co-author",
    ],
    "submission_withdrawal": [
        "withdraw", "withdrawal", "retract", "remove my submission",
        "pull our paper", "delete our submission", "data deletion",
        "withdraw submission", "after acceptance",
    ],
    "ethics_concern": [
        "plagiarism", "plagiar", "ethics", "ethical", "violation",
        "misconduct", "irb", "human subjects", "consent", "confidentiality",
        "breach", "dual submission", "fabricat", "undisclosed conflict",
        "report",
    ],
    "technical_issue": [
        "upload", "error", "cannot log in", "can't log in", "login",
        "password reset", "portal", "system", "failed", "broken", "bug",
        "wrong title", "missing file", "submission system",
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
            intent="general_inquiry",
            confidence=0.3,
            reasoning="No policy keywords matched; defaulting to general_inquiry.",
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

            return get_trainable_classifier().classify(subject, email_text)
        return keyword_classify(subject, email_text)

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
