"""add_policy_conflict_report

Adds a nullable ``conflict_report`` (JSON) column to ``policy_documents`` — the
last LLM conflict report computed when a policy is created / edited /
reactivated / re-checked (KB item 2e). Shape mirrors
``app/pipeline/policy_conflict.py::ConflictReport``. NULL ⇒ never checked.

Additive and reversible; mirrors the batch_alter_table pattern of
a1b2c3d4e5f6_add_policy_intents.py.

Revision ID: d5e6f7a8b9c0
Revises: af44a007c60f
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, Sequence[str], None] = 'af44a007c60f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — add the nullable conflict_report column."""
    with op.batch_alter_table('policy_documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('conflict_report', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema — drop the conflict_report column."""
    with op.batch_alter_table('policy_documents', schema=None) as batch_op:
        batch_op.drop_column('conflict_report')
