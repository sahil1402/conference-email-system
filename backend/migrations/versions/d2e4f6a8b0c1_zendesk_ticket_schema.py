"""zendesk_ticket_schema

Piece 3 of the Zendesk integration: schema only (no API code). Extends the
existing ``emails`` table with Zendesk ticket fields (a ticket maps 1:1 onto an
Email row) and adds the ``email_thread_messages`` child table for the comment
thread. See ZENDESK_API.md §3 (comments / public flag), §6 (author role), and
§10 (ticket.id dedup key).

Revision ID: d2e4f6a8b0c1
Revises: a7b8c9d0e1f2
Create Date: 2026-07-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d2e4f6a8b0c1"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- Zendesk fields on the existing emails table -----------------------
    # batch_alter_table so SQLite (limited ALTER) is handled the same way as the
    # Phase 6A chairs migration; PostgreSQL emits direct ALTERs.
    with op.batch_alter_table("emails", schema=None) as batch_op:
        # NOT NULL with a server_default so the ~existing rows backfill to
        # "toy_dataset" without a manual data step.
        batch_op.add_column(
            sa.Column(
                "source",
                sa.String(length=32),
                nullable=False,
                server_default="toy_dataset",
            )
        )
        batch_op.add_column(
            sa.Column("zendesk_ticket_id", sa.BigInteger(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("zendesk_requester_id", sa.BigInteger(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("zendesk_status", sa.String(length=16), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "zendesk_created_at", sa.DateTime(timezone=True), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "zendesk_updated_at", sa.DateTime(timezone=True), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column("last_processed_comment_id", sa.BigInteger(), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_emails_source"), ["source"], unique=False
        )
        # Unique so a Zendesk ticket upserts to exactly one row (§10). Multiple
        # NULLs are permitted (non-Zendesk rows) in both SQLite and PostgreSQL.
        batch_op.create_index(
            batch_op.f("ix_emails_zendesk_ticket_id"),
            ["zendesk_ticket_id"],
            unique=True,
        )

    # --- New child table: one row per thread message (comment) -------------
    op.create_table(
        "email_thread_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email_id", sa.Integer(), nullable=False),
        sa.Column("zendesk_comment_id", sa.BigInteger(), nullable=True),
        sa.Column("public", sa.Boolean(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=True),
        sa.Column("author_role", sa.String(length=16), nullable=True),
        sa.Column("plain_body", sa.Text(), nullable=True),
        sa.Column("html_body", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("via_channel", sa.String(length=32), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["email_id"],
            ["emails.id"],
            name="fk_email_thread_messages_email_id_emails",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("email_thread_messages", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_email_thread_messages_zendesk_comment_id"),
            ["zendesk_comment_id"],
            unique=True,
        )
        batch_op.create_index(
            "ix_email_thread_messages_email_id_created_at",
            ["email_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("email_thread_messages", schema=None) as batch_op:
        batch_op.drop_index("ix_email_thread_messages_email_id_created_at")
        batch_op.drop_index(
            batch_op.f("ix_email_thread_messages_zendesk_comment_id")
        )
    op.drop_table("email_thread_messages")

    with op.batch_alter_table("emails", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_emails_zendesk_ticket_id"))
        batch_op.drop_index(batch_op.f("ix_emails_source"))
        batch_op.drop_column("last_processed_comment_id")
        batch_op.drop_column("zendesk_updated_at")
        batch_op.drop_column("zendesk_created_at")
        batch_op.drop_column("zendesk_status")
        batch_op.drop_column("zendesk_requester_id")
        batch_op.drop_column("zendesk_ticket_id")
        batch_op.drop_column("source")
