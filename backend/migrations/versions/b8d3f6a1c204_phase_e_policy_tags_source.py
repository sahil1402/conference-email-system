"""phase_e_policy_tags_source

Adds ``tags`` (JSON list) and ``source`` (String) columns to
``policy_documents`` so the DB-backed FAISS retriever reaches tag parity with
the file-backed BM25 retriever. Both columns are nullable: the pre-Phase-E
dummy rows carry neither until the DB is reseeded (Phase F). No data is
written or reseeded here — schema only.

Revision ID: b8d3f6a1c204
Revises: 1f51f0224943
Create Date: 2026-07-10 09:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8d3f6a1c204'
down_revision: Union[str, Sequence[str], None] = '1f51f0224943'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('policy_documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tags', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('source', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('policy_documents', schema=None) as batch_op:
        batch_op.drop_column('source')
        batch_op.drop_column('tags')
