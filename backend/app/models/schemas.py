"""Pydantic v2 API / domain schemas.

These models define the contracts passed between modules and over the API.
They are intentionally logic-free — validation only. The ORM models in
`app.db.models` map to/from these (Piece 5).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import (
    EmailStatus,
    RoutingLane,
    SensitivityLevel,
)


# ---------------------------------------------------------------------------
# Inbound payloads
# ---------------------------------------------------------------------------
class EmailIn(BaseModel):
    """An inbound conference email as received by the system."""

    sender: EmailStr = Field(..., description="Email address of the sender.")
    sender_name: str | None = Field(
        default=None, description="Display name of the sender, if known."
    )
    subject: str = Field(..., description="Email subject line.")
    body: str = Field(..., description="Plain-text body of the email.")
    received_at: datetime | None = Field(
        default=None, description="When the email was received (server time if omitted)."
    )


# ---------------------------------------------------------------------------
# Pipeline result sub-objects
# ---------------------------------------------------------------------------
class IntentMatch(BaseModel):
    """A single candidate intent and its score from the classifier."""

    intent: str
    score: float = Field(..., ge=0.0, le=1.0, description="Match score in [0, 1].")


class ClassificationResult(BaseModel):
    """Output of the Classifier module."""

    intent: str = Field(..., description="Best-guess intent.")
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence of the chosen intent."
    )
    reasoning: str = Field(
        default="", description="Human-readable rationale for the classification."
    )
    top_matches: list[IntentMatch] = Field(
        default_factory=list, description="Ranked alternative intents with scores."
    )


class RoutingDecision(BaseModel):
    """Output of the Router module."""

    lane: RoutingLane = Field(..., description="Destination lane.")
    sensitivity: SensitivityLevel = Field(
        ..., description="Assessed content sensitivity."
    )
    reason: str = Field(default="", description="Why this routing decision was made.")
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence in the routing decision."
    )


class PolicyCitation(BaseModel):
    """A citation to a policy / FAQ document grounding a draft."""

    policy_id: str = Field(..., description="Identifier of the cited policy document.")
    title: str = Field(..., description="Human-readable title of the policy.")
    snippet: str = Field(..., description="The exact text excerpt cited.")
    score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Relevance score, if available."
    )


class RetrievalContextItem(BaseModel):
    """A single retrieved passage used as grounding context for drafting."""

    policy_id: str = Field(..., description="Source policy document id.")
    title: str = Field(..., description="Source policy document title.")
    content: str = Field(..., description="Retrieved passage content.")
    score: float = Field(..., ge=0.0, le=1.0, description="Retrieval relevance score.")


class DraftResponse(BaseModel):
    """Output of the Drafter module — a grounded reply draft."""

    draft_body: str = Field(..., description="Generated reply text.")
    policy_citations: list[PolicyCitation] = Field(
        default_factory=list, description="Policies cited in the draft."
    )
    retrieval_context: list[RetrievalContextItem] = Field(
        default_factory=list,
        description="Passages retrieved as grounding for the draft.",
    )


# ---------------------------------------------------------------------------
# Persisted record
# ---------------------------------------------------------------------------
class EmailRecord(BaseModel):
    """Full lifecycle record for an email, including pipeline outputs."""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Primary key.")
    sender: EmailStr
    sender_name: str | None = None
    subject: str
    body: str
    received_at: datetime
    status: EmailStatus = Field(default=EmailStatus.PENDING)

    classification: ClassificationResult | None = None
    routing: RoutingDecision | None = None
    draft: DraftResponse | None = None

    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Human-review actions
# ---------------------------------------------------------------------------
class ApprovalAction(BaseModel):
    """A chair's action on a human-review email."""

    action: Literal["approve", "edit", "reroute"] = Field(
        ..., description="The action taken by the chair."
    )
    edited_body: str | None = Field(
        default=None, description="Edited reply body (required when action='edit')."
    )
    reroute_reason: str | None = Field(
        default=None,
        description="Reason for rerouting (required when action='reroute').",
    )
