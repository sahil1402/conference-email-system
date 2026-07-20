"""drop_policy_tags

Drops the ``tags`` (JSON list) column from ``policy_documents``. E007
(docs/exp_tracking/E007_policy_tag_ablation.md) showed the auto-generated tags
carry no retrieval signal, so the whole tag path is retired. The application-side
tag code is *commented out* (not deleted) so a future controlled facet vocabulary
(CLASSIFICATION_REWORK.md B4b) can restore it; the downgrade here re-adds the
column with its original nullable-JSON shape.

``source`` is untouched (it predates and outlives tags).

Revision ID: e7a9c1f2b3d4
Revises: f7a1b2c3d4e5
Create Date: 2026-07-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a9c1f2b3d4'
down_revision: Union[str, Sequence[str], None] = 'f7a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — drop the tags column."""
    with op.batch_alter_table('policy_documents', schema=None) as batch_op:
        batch_op.drop_column('tags')


def downgrade() -> None:
    """Downgrade schema — re-add the nullable tags column (data not restored)."""
    with op.batch_alter_table('policy_documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tags', sa.JSON(), nullable=True))
