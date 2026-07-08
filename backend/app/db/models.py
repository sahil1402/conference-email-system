"""SQLAlchemy ORM models (the persistence layer).

These tables back the domain. Nested pipeline outputs (classification, routing,
draft) are stored as JSON on the Email row for the MVP; they can be normalized
into their own tables later without changing the Pydantic contracts.
"""

from datetime import datetime

from sqlalchemy import Boolean, JSON, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models.enums import EmailStatus


class Email(Base):
    """An incoming conference email and its full lifecycle state."""

    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    sender: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    sender_name: Mapped[str | None] = mapped_column(String(320), nullable=True)
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=EmailStatus.PENDING.value,
        index=True,
    )

    # Pipeline outputs, serialized from their Pydantic models.
    classification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    routing: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    draft: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Which chair a human-review email is assigned to (Phase 6A). Nullable:
    # FAQ-lane emails are never assigned, and the column stays empty until the
    # chair router runs. FK is nullable so deleting a chair does not cascade-
    # delete its emails (SET NULL semantics are enforced at the app layer).
    assigned_chair_id: Mapped[int | None] = mapped_column(
        ForeignKey("chairs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Chair(Base):
    """A conference chair who can be assigned human-review emails (Phase 6A).

    ``areas`` is a JSON list of intent/topic strings the chair owns; the chair
    router matches a classified email's intent against these. A chair with an
    empty ``areas`` list acts as the catch-all fallback (the General Chair),
    receiving anything no other active chair claims. ``active`` gates a chair
    out of routing without deleting the row (and its assignment history).
    """

    __tablename__ = "chairs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role_title: Mapped[str] = mapped_column(String(255), nullable=False)
    # List of intent/topic strings this chair handles (empty = fallback).
    areas: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AuditLog(Base):
    """An append-only record of actions taken on an email."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email_id: Mapped[int] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Free-form structured context. Column is named "metadata"; the Python
    # attribute is renamed because `metadata` is reserved on the declarative base.
    extra_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSON, nullable=True
    )


class PolicyDocument(Base):
    """A FAQ / policy knowledge-base entry used for grounding replies."""

    __tablename__ = "policy_documents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    policy_key: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
