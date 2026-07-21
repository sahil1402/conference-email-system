"""email_processing_results (Piece T1)

Per-message pipeline results. A requester follow-up on a Zendesk thread
(``email_thread_messages``) gets its own classify -> retrieve -> route -> draft
cycle whose result is stored HERE, separately from the parent Email's result so
the original is never overwritten. One row per processed message
(``thread_message_id`` unique, CASCADE with the message).

Schema only — no trigger logic / API / frontend, and NO backfill: the table
starts empty and is populated only by future per-message processing.

Revision ID: d4e5f6a7b8c9
Revises: b2c3d4e5f6a7
Create Date: 2026-07-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "email_processing_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("thread_message_id", sa.Integer(), nullable=False),
        sa.Column("classification", sa.JSON(), nullable=True),
        sa.Column("routing", sa.JSON(), nullable=True),
        sa.Column("draft", sa.JSON(), nullable=True),
        sa.Column("retrieval_context", sa.JSON(), nullable=True),
        sa.Column("lane", sa.String(length=32), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["thread_message_id"],
            ["email_thread_messages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("email_processing_results", schema=None) as batch_op:
        # Unique: exactly one result row per thread message.
        batch_op.create_index(
            batch_op.f("ix_email_processing_results_thread_message_id"),
            ["thread_message_id"],
            unique=True,
        )
        batch_op.create_index(
            batch_op.f("ix_email_processing_results_lane"),
            ["lane"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("email_processing_results", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_email_processing_results_lane"))
        batch_op.drop_index(
            batch_op.f("ix_email_processing_results_thread_message_id")
        )
    op.drop_table("email_processing_results")
