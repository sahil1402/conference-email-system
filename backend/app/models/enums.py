"""Domain enumerations.

These are the controlled vocabularies shared across the pipeline (classifier,
router, drafter), the persistence layer, and the API. Keep them stable — the
DB and frontend depend on these string values.
"""

from enum import Enum


class EmailIntent(str, Enum):
    """High-level classification of an incoming email's intent."""

    FAQ_DEADLINE = "FAQ_DEADLINE"
    FAQ_FORMAT = "FAQ_FORMAT"
    FAQ_SUBMISSION = "FAQ_SUBMISSION"
    REVIEW_ASSIGNMENT = "REVIEW_ASSIGNMENT"
    VISA_LETTER = "VISA_LETTER"
    APPEAL = "APPEAL"
    AMBIGUOUS = "AMBIGUOUS"
    OTHER = "OTHER"


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
    APPROVED = "APPROVED"
    SENT = "SENT"
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
