"""email_processing_results: allow history per message (Piece T1b)

T1 made ``thread_message_id`` unique (one result per message, enforced by the
unique index ``ix_email_processing_results_thread_message_id``). T1b allows
multiple results per message so a follow-up can be reprocessed and keep its
full history (mirroring the parent Email's manual redraft). This drops the
UNIQUE index and recreates a plain (non-unique) index of the same name, so the
column stays indexed for parent lookups.

Schema only — no data touched (the table is populated only by future
per-message processing).

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "email_processing_results"
_INDEX = "ix_email_processing_results_thread_message_id"


def upgrade() -> None:
    """Drop the UNIQUE index; recreate it non-unique (keep the FK indexed)."""
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.drop_index(batch_op.f(_INDEX))
        batch_op.create_index(
            batch_op.f(_INDEX), ["thread_message_id"], unique=False
        )


def downgrade() -> None:
    """Restore the UNIQUE index (T1 behaviour: one result per message)."""
    with op.batch_alter_table(_TABLE, schema=None) as batch_op:
        batch_op.drop_index(batch_op.f(_INDEX))
        batch_op.create_index(
            batch_op.f(_INDEX), ["thread_message_id"], unique=True
        )
