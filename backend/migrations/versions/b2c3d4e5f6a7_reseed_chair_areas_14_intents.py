"""reseed_chair_areas_14_intents

The intent taxonomy moved from the Phase 6A set (submission_deadline,
formatting_requirements, submission_withdrawal, review_assignment,
technical_issue, ethics_concern, authorship_dispute, general_inquiry,
sponsorship, publicity, media_inquiry) to the 14-intent taxonomy. The
``chairs.areas`` seeded by 1f51f0224943_phase6a_chairs.py still hold the OLD
intent names, so multi-chair routing (currently gated off, see
CHAIR_ROUTING_STRATEGY) would be stale the moment it is enabled.

This is a forward DATA migration only — no schema change. It re-seeds the
``areas`` of the same five standing chairs (matched by their exact ``name``
string from the Phase 6A seed) to the new 14-intent families. The mapping
mirrors the test fixture ``_SEED_CHAIRS`` in
tests/test_chair_routing_integration.py and ``CHAIR_AREAS`` in
scripts/bench_real.py (both already written against the 14-intent taxonomy;
confirmed to agree with this migration's mapping before writing it).

``cms_support`` (one of the 14 intents) is intentionally left unmapped — it
falls through to the General Chair's empty-``areas`` fallback, same as any
other unmapped intent.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


chairs_table = sa.table(
    "chairs",
    sa.column("name", sa.String),
    sa.column("areas", sa.JSON),
)

# New 14-intent family per chair (upgrade target).
NEW_AREAS = {
    "Program Chair": [
        "author_profile_compliance",
        "submission_upload_help",
        "submission_requirements",
        "submission_format_policy",
        "author_list_change",
    ],
    "Diversity & Ethics Chair": [
        "review_decision_appeal",
        "desk_reject_appeal",
        "anonymity_violation",
    ],
    "Local Arrangements Chair": [
        "reviewer_assignment",
        "review_submission_help",
        "paper_bidding",
    ],
    "Publicity/Sponsorship Chair": [
        "reviewer_workload_role",
        "committee_invitation",
    ],
    "General Chair": [],
}

# Original Phase 6A areas (verbatim from 1f51f0224943_phase6a_chairs.py), for
# the downgrade path.
ORIGINAL_AREAS = {
    "Program Chair": [
        "submission_deadline",
        "formatting_requirements",
        "submission_withdrawal",
        "review_assignment",
        "technical_issue",
    ],
    "Diversity & Ethics Chair": ["ethics_concern", "authorship_dispute"],
    "Local Arrangements Chair": ["general_inquiry"],
    "Publicity/Sponsorship Chair": ["sponsorship", "publicity", "media_inquiry"],
    "General Chair": [],
}


def _apply(areas_by_name: dict[str, list[str]]) -> None:
    for name, areas in areas_by_name.items():
        op.execute(
            chairs_table.update()
            .where(chairs_table.c.name == name)
            .values(areas=areas)
        )


def upgrade() -> None:
    """Re-seed chairs.areas to the 14-intent taxonomy families."""
    _apply(NEW_AREAS)


def downgrade() -> None:
    """Restore the original Phase 6A areas."""
    _apply(ORIGINAL_AREAS)
