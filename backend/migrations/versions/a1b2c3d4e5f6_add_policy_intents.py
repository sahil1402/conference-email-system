"""add_policy_intents

Adds a nullable ``intents`` (JSON list) column to ``policy_documents``. Part of
Task B (docs/local/CLASSIFICATION_REWORK.md): a persistent intent→KB-chunk
coverage map. This column is populated offline per-chunk (Task B3, LLM-labeled
against the controlled vocabulary in ``app/pipeline/taxonomy.py::VALID_INTENTS``)
and read by retrieval (soft score boost) and FAQ-eligibility (Task B2/B5).

Mirrors the batch_alter_table pattern of e7a9c1f2b3d4_drop_policy_tags.py.

Revision ID: a1b2c3d4e5f6
Revises: e7a9c1f2b3d4
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'e7a9c1f2b3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — add the nullable intents column."""
    with op.batch_alter_table('policy_documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('intents', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema — drop the intents column."""
    with op.batch_alter_table('policy_documents', schema=None) as batch_op:
        batch_op.drop_column('intents')
