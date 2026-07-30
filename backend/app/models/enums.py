"""Domain enumerations.

These are the controlled vocabularies shared across the pipeline (classifier,
router, drafter), the persistence layer, and the API. Keep them stable — the
DB and frontend depend on these string values.
"""

from enum import Enum


class RoutingLane(str, Enum):
    """The two-lane workflow destination."""

    AUTO_REPLY = "AUTO_REPLY"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class SensitivityLevel(str, Enum):
    """How sensitive the email content is (drives routing / escalation)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class EmailStatus(str, Enum):
    """Lifecycle state of an email as it moves through the system."""

    PENDING = "PENDING"
    CLASSIFIED = "CLASSIFIED"
    ROUTED = "ROUTED"
    DRAFT_GENERATED = "DRAFT_GENERATED"
    # The drafter call SUCCEEDED but the model exhausted its completion budget
    # before emitting any visible reply (reasoning models spend tokens thinking
    # first), so there is no draft to review. Distinct from ROUTED (used when the
    # drafter genuinely errored) so this recoverable, retryable condition is not
    # silently recorded as a finished draft. Like SOLVED / SEND_FAILED below it's
    # a plain string in the String(32) status column, so no migration is needed.
    DRAFT_TRUNCATED = "DRAFT_TRUNCATED"
    APPROVED = "APPROVED"
    SENT = "SENT"
    # The ticket was resolved WITHOUT sending a reply (the chair judged no
    # response was warranted and just closed it). Distinct from SENT — nothing
    # went to the requester. Like SEND_FAILED below, it's a plain string in the
    # String(32) status column, so no migration is required to add it.
    SOLVED = "SOLVED"
    # A transport (e.g. Zendesk write-back) was attempted but failed; the draft
    # is preserved and the send is re-triable. Stored as a plain string in the
    # String(32) status column, so no migration is required to add it.
    SEND_FAILED = "SEND_FAILED"
    ARCHIVED = "ARCHIVED"


class UserRole(str, Enum):
    """Roles for people operating the system."""

    CHAIR = "CHAIR"
    REVIEWER = "REVIEWER"
    ADMIN = "ADMIN"


class EmailSource(str, Enum):
    """Origin of an email/ticket record (which ingestion path created it)."""

    TOY_DATASET = "toy_dataset"
    ZENDESK = "zendesk"


class MessageAuthorRole(str, Enum):
    """Zendesk comment author role — how a thread message's writer is classed.

    Mirrors Zendesk's user roles (see ZENDESK_API.md §6): ``end-user`` is the
    requester/author; ``agent``/``admin`` are chairs/staff. This is how a chair's
    reply is told apart from the author's message inside a thread.
    """

    END_USER = "end-user"
    AGENT = "agent"
    ADMIN = "admin"
