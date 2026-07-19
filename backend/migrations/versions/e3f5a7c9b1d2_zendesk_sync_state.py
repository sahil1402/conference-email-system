"""zendesk_sync_state

Piece 4 of the Zendesk integration: the poller checkpoint table. One row per
Zendesk account (keyed by ``subdomain``) holding the incremental-export resume
cursor plus light bookkeeping, so a restart resumes where it left off (and the
single well-known row can be row-locked later for multi-instance safety). See
ZENDESK_API.md §2 / §10. Schema only — no API code.

Revision ID: e3f5a7c9b1d2
Revises: d2e4f6a8b0c1
Create Date: 2026-07-19 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e3f5a7c9b1d2"
down_revision: Union[str, Sequence[str], None] = "d2e4f6a8b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "zendesk_sync_state",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("subdomain", sa.String(length=255), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("start_time", sa.BigInteger(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "tickets_seen",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("zendesk_sync_state", schema=None) as batch_op:
        # Unique so there is exactly one checkpoint row per Zendesk account.
        batch_op.create_index(
            batch_op.f("ix_zendesk_sync_state_subdomain"),
            ["subdomain"],
            unique=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("zendesk_sync_state", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_zendesk_sync_state_subdomain"))
    op.drop_table("zendesk_sync_state")
