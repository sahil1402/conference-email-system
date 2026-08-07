"""add extraction json to emails and email_processing_results

Adds a nullable ``extraction`` (JSON) column to BOTH ``emails`` and
``email_processing_results`` — which submission an email is about and who it
names. Shape mirrors ``app/pipeline/extractor.py::ExtractionResult``:
{"submission_number", "openreview_forum_id", "authors", "method"}.

NOTE (later change, recorded here so this file is not read as current): the two
identifier fields were subsequently widened to LISTS —
``submission_numbers`` / ``openreview_forum_ids`` — because an email may name
several submissions. That changed only the dict stored INSIDE this JSON column,
never the column itself, so it needed no migration of its own. The shape
described above is what this revision introduced, not what is stored today.

NULL ⇒ the row was processed before this column existed ("never looked"), which
is deliberately distinguishable from a stored extraction whose
``submission_number`` is null ("looked, found none"). Every reader must tolerate
NULL; nothing backfills it.

Both tables get the column because a follow-up turn can name a different
submission than the opening inquiry, so the per-message result carries its own.

Additive and reversible; mirrors the batch_alter_table pattern of
d5e6f7a8b9c0_add_policy_conflict_report.py.

Revision ID: 57b59f3ef990
Revises: f8b2c4d6e0a1
Create Date: 2026-08-06 17:46:53.451783

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '57b59f3ef990'
down_revision: Union[str, Sequence[str], None] = 'f8b2c4d6e0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — add the nullable extraction column to both tables."""
    with op.batch_alter_table('email_processing_results', schema=None) as batch_op:
        batch_op.add_column(sa.Column('extraction', sa.JSON(), nullable=True))

    with op.batch_alter_table('emails', schema=None) as batch_op:
        batch_op.add_column(sa.Column('extraction', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema — drop the extraction column from both tables."""
    with op.batch_alter_table('emails', schema=None) as batch_op:
        batch_op.drop_column('extraction')

    with op.batch_alter_table('email_processing_results', schema=None) as batch_op:
        batch_op.drop_column('extraction')
