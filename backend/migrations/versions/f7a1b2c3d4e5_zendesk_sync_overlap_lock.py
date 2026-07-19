"""zendesk_sync_overlap_lock

Adds the single-flight overlap-guard columns to ``zendesk_sync_state``:
``is_running`` (a cycle holds the lock) and ``running_since`` (when it claimed
it, powering the staleness takeover). See the ingest adapter's overlap guard.

Revision ID: f7a1b2c3d4e5
Revises: c361c1d3ad79
Create Date: 2026-07-19 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f7a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "c361c1d3ad79"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("zendesk_sync_state", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_running",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "running_since", sa.DateTime(timezone=True), nullable=True
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("zendesk_sync_state", schema=None) as batch_op:
        batch_op.drop_column("running_since")
        batch_op.drop_column("is_running")
