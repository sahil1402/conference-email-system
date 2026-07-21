"""SQLAlchemy ORM models (the persistence layer).

These tables back the domain. Nested pipeline outputs (classification, routing,
draft) are stored as JSON on the Email row for the MVP; they can be normalized
into their own tables later without changing the Pydantic contracts.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base
from app.models.enums import EmailSource, EmailStatus


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

    # Re-evaluation (Phase G). ``retrieval_context`` captures the exact retriever
    # inputs at ingest — {"query": str, "intent": str, "retrieved_ids": [...]} —
    # so a KB-change sweep can re-run retrieval with no model call and compare the
    # grounding set. ``redrafting`` is the transient in-progress flag surfaced as
    # the "re-drafting…" badge; set when queued, cleared when the new draft lands.
    retrieval_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    redrafting: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false(), index=True
    )

    # Which chair a human-review email is assigned to (Phase 6A). Nullable:
    # FAQ-lane emails are never assigned, and the column stays empty until the
    # chair router runs. FK is nullable so deleting a chair does not cascade-
    # delete its emails (SET NULL semantics are enforced at the app layer).
    assigned_chair_id: Mapped[int | None] = mapped_column(
        ForeignKey("chairs.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # --- Zendesk origin fields (Piece 3) ----------------------------------
    # A Zendesk ticket maps 1:1 onto an Email row; these columns hold the
    # ticket-specific state. All are nullable so pre-Zendesk (toy/synthetic)
    # rows are unaffected. See ZENDESK_API.md §10 (dedup) / §2 (status).
    #
    # Origin discriminator: "zendesk" vs "toy_dataset". Server default backfills
    # existing rows as toy_dataset; Python default keeps ORM inserts in agreement.
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=EmailSource.TOY_DATASET.value,
        server_default=EmailSource.TOY_DATASET.value,
        index=True,
    )
    # Canonical Zendesk dedup key (§10). Unique so a ticket upserts to one row;
    # multiple NULLs are allowed (SQLite & Postgres) for non-Zendesk rows.
    zendesk_ticket_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, unique=True, index=True
    )
    # Zendesk user id of the requester (join to users); sender/sender_name hold
    # the email + display name.
    zendesk_requester_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Zendesk lifecycle status (new/open/pending/hold/solved/closed) — distinct
    # from our internal EmailStatus. Lets us treat "closed" as read-only.
    zendesk_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The ticket's own timestamps in Zendesk (kept separate from created_at /
    # updated_at below, which are when WE created/updated this row).
    zendesk_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Compared on each poll to skip re-processing an unchanged ticket (§10).
    zendesk_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Highest end-user comment id already processed, so the poller only drafts on
    # a genuinely new author comment rather than every ticket update (§10).
    last_processed_comment_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )

    # Thread messages (Zendesk comments), ordered oldest-first. The initial
    # inquiry is the first public end-user message in this order (see §3/§5) —
    # derived by query, not stored, per the agreed design.
    thread_messages: Mapped[list["EmailThreadMessage"]] = relationship(
        "EmailThreadMessage",
        back_populates="email",
        order_by="EmailThreadMessage.created_at",
        cascade="all, delete-orphan",
        passive_deletes=True,
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


class EmailThreadMessage(Base):
    """One message (Zendesk comment) in an email/ticket thread (Piece 3).

    A child of :class:`Email`. The ``public`` flag is load-bearing: ``True`` is a
    real reply visible to the requester, ``False`` an internal note (ZENDESK_API.md
    §3). ``author_role`` (end-user/agent/admin) tells the author's message apart
    from a chair's reply. Only the initial inquiry — the first ``public`` end-user
    message ordered by ``created_at`` — is (re)classified; the rest are context.
    """

    __tablename__ = "email_thread_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email_id: Mapped[int] = mapped_column(
        ForeignKey("emails.id", ondelete="CASCADE"), nullable=False
    )
    # Zendesk comment id — globally unique (§3), so a comment upserts to one row.
    zendesk_comment_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, unique=True, index=True
    )
    # True = public reply to the requester; False = internal note.
    public: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Zendesk user id + role of the comment author (§6).
    author_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    author_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Sanitized plain text (safest for the classifier/drafter) + rendered HTML.
    plain_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    html_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The comment's own timestamp in Zendesk — the thread ordering key.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Channel the comment arrived through, e.g. "email" (§3 via.channel).
    via_channel: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # When WE stored the row (distinct from the comment's created_at above).
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    email: Mapped["Email"] = relationship("Email", back_populates="thread_messages")

    # Per-message pipeline results (Piece T1 / T1b). A follow-up message gets
    # its own classify -> retrieve -> route -> draft cycle stored in its own row
    # here, separately from the parent Email's result. A message may be
    # reprocessed (mirroring the parent Email's manual redraft), so this is
    # one-to-many: each reprocess appends a new row, keeping the full history.
    # Ordered chronologically (oldest-first, latest last) to match the parent
    # Email.thread_messages relationship and the audit trail. Starts empty until
    # a later piece populates it.
    processing_results: Mapped[list["EmailProcessingResult"]] = relationship(
        "EmailProcessingResult",
        back_populates="thread_message",
        order_by="EmailProcessingResult.created_at, EmailProcessingResult.id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # Serves both parent-lookup and the oldest-first ordering scan used to
        # find the initial inquiry, in one index.
        Index(
            "ix_email_thread_messages_email_id_created_at", "email_id", "created_at"
        ),
    )


class EmailProcessingResult(Base):
    """A pipeline result for a single thread follow-up message (Piece T1 / T1b).

    The parent :class:`Email` row stores the pipeline outputs for the *initial*
    inquiry. When a requester posts a follow-up — a later public end-user
    :class:`EmailThreadMessage` — that message gets its own
    classify -> retrieve -> route -> draft cycle, and its result is stored HERE,
    separately, so the original Email result is never overwritten.

    Multiple rows may reference the same ``thread_message_id`` (T1b): a message
    can be reprocessed (mirroring the parent Email's manual redraft), and each
    reprocess appends a new row so the full history is retained — the latest is
    the newest by ``created_at`` / ``id``. This table starts empty; only new
    processing (wired in a later piece) ever populates it. The JSON columns
    deliberately mirror the shapes the parent Email stores (``classification`` /
    ``routing`` / ``draft`` / ``retrieval_context``) so the same Pydantic
    serialization can be reused; ``lane`` and ``confidence`` are denormalized
    scalars for cheap filtering/sorting.
    """

    __tablename__ = "email_processing_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Non-unique FK (T1b): a message may have several results over time. CASCADE
    # so results die with their message (and, transitively, the parent Email).
    thread_message_id: Mapped[int] = mapped_column(
        ForeignKey("email_thread_messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Pipeline outputs — same JSON shapes the parent Email row stores.
    classification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    routing: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    draft: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # {"query": str, "intent": str, "retrieved_ids": [...]} — matches Email.
    retrieval_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Denormalized scalars (also derivable from the JSON above): the routing lane
    # ("AUTO_REPLY" / "HUMAN_REVIEW") and the classifier confidence.
    lane: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    thread_message: Mapped["EmailThreadMessage"] = relationship(
        "EmailThreadMessage", back_populates="processing_results"
    )


class ZendeskSyncState(Base):
    """Checkpoint for the Zendesk incremental-export poller (Piece 4).

    One row per Zendesk account (keyed by ``subdomain``). Holds the resume
    ``cursor`` (the incremental export's ``after_cursor``; NULL → the next poll
    starts from ``start_time``) plus light bookkeeping. Persisting the cursor in
    the DB (not a local file like the one-off pull script) is what lets the poll
    survive restarts and, later, be made multi-instance-safe by row-locking this
    single well-known row (``SELECT ... FOR UPDATE``). See ZENDESK_API.md §2/§10.
    """

    __tablename__ = "zendesk_sync_state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    subdomain: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    # Incremental export resume point; NULL means "first run — use start_time".
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Unix epoch for the initial call before any cursor exists.
    start_time: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # When the last successful cycle completed, and the last error (if any).
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Cumulative count of tickets seen across cycles (bookkeeping only).
    tickets_seen: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )

    # --- Overlap guard (single-flight lock) -------------------------------
    # A cycle sets ``is_running`` True (stamping ``running_since``) while it
    # holds the row; a second trigger sees the flag and skips instead of racing.
    # ``running_since`` also powers a staleness takeover: if a claimed run went
    # quiet longer than the stale window (a crash mid-cycle), a new run may
    # reclaim the lock rather than staying blocked forever.
    is_running: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    running_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    # [tags-dropped E007] The ``tags`` column is dropped by migration
    # e7a9c1f2b3d4. E007 (docs/exp_tracking/E007_policy_tag_ablation.md) showed the
    # auto-generated tags carry no retrieval signal; the whole tag path is commented
    # out (not deleted) so B4b (a controlled facet vocab) can restore it — uncomment
    # this column + re-add it in an up-migration.
    # tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Intents this chunk can answer (Task B, controlled vocab from
    # taxonomy.VALID_INTENTS).
    intents: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Phase F (layered KB): the layer/trust axis and the soft on/off lifecycle.
    # visibility: "public" (official corpus, freely citable) | "internal"
    # (chair-authored, not on the public site — retrievable & citable, marked for
    # provenance). status: "active" (indexed) | "inactive" (retired, not indexed).
    # Both DB + Python defaults so raw migrations and ORM inserts agree.
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default="public", server_default="public", index=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active", index=True
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


class PolicyAuditLog(Base):
    """Append-only record of KB governance actions (create / edit / retire)."""

    __tablename__ = "policy_audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    policy_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
