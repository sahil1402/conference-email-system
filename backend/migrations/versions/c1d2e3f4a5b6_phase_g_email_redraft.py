"""phase G: emails.redrafting + emails.retrieval_context

Revision ID: c1d2e3f4a5b6
Revises: a7b8c9d0e1f2
Create Date: 2026-07-19

Adds the two columns the re-evaluate-on-policy-change sweep needs:
- retrieval_context (JSON, nullable): the exact retriever inputs captured at
  ingest so the sweep re-runs retrieval with no model call.
- redrafting (Boolean, default False): transient in-progress flag for the UI.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("emails", sa.Column("retrieval_context", sa.JSON(), nullable=True))
    op.add_column(
        "emails",
        sa.Column(
            "redrafting",
            sa.Boolean(),
            nullable=False,
            # Dialect-portable boolean default: renders `0` on SQLite and `false`
            # on Postgres (a bare `0`/`text("0")` is an integer default Postgres
            # rejects for a boolean column).
            server_default=sa.false(),
        ),
    )
    op.create_index("ix_emails_redrafting", "emails", ["redrafting"])


def downgrade() -> None:
    op.drop_index("ix_emails_redrafting", table_name="emails")
    op.drop_column("emails", "redrafting")
    op.drop_column("emails", "retrieval_context")
