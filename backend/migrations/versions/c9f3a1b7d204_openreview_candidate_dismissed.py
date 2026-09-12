"""add openreview_candidate_dismissed to emails

A chair's judgment that an email flagged as an OpenReview reply is a FALSE
POSITIVE of the text-based detection. Set by
``POST /emails/{id}/dismiss-openreview-candidate``; the row then falls through
the normal ``/queue`` predicate instead of ``/queue/openreview``, keeping
whatever routing lane it already had.

WHY A COLUMN AND NOT A KEY INSIDE ``extraction``
------------------------------------------------
``emails.extraction`` is overwritten wholesale on every pipeline pass —
``orchestrator._compute`` persists ``ExtractionResult.model_dump()``, and
``openreview_reply_candidate`` is a ``@computed_field`` recomputed there from
``openreview_note_id`` + ``openreview_notification_sender``. The email body does
not change, so it recomputes to True. A dismissal stored inside that JSON would
be silently reverted by the next follow-up reply, manual re-draft, or KB-change
sweep, with nothing in the audit trail to explain the reappearance.

``extraction`` records what the pipeline OBSERVED; this records what a human
DECIDED. Different kinds of fact, different lifetimes: the observation should be
recomputed whenever the text is reprocessed, and the decision has to survive
exactly that. A separate column is what makes ``_compute`` structurally unable
to reach it — no carry-forward logic to remember and get wrong later.

NOT NULL with ``server_default=false()``, so every existing row backfills to
"not dismissed" — the behaviour before this column existed. Nullable would have
introduced a third state ("unknown") that the queue predicate would then have to
disambiguate under SQL three-valued logic, exactly the trap
``_unresolved_openreview_candidate`` documents.

Additive and reversible; mirrors the batch_alter_table pattern of
57b59f3ef990_add_extraction_json_to_emails_and_email_.py.

Revision ID: c9f3a1b7d204
Revises: 57b59f3ef990
Create Date: 2026-09-11 09:12:41.882170

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9f3a1b7d204'
down_revision: Union[str, Sequence[str], None] = '57b59f3ef990'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — add the dismissal flag, defaulting to not-dismissed."""
    with op.batch_alter_table('emails', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'openreview_candidate_dismissed',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_index(
            batch_op.f('ix_emails_openreview_candidate_dismissed'),
            ['openreview_candidate_dismissed'],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema — drop the dismissal flag and its index."""
    with op.batch_alter_table('emails', schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f('ix_emails_openreview_candidate_dismissed')
        )
        batch_op.drop_column('openreview_candidate_dismissed')
