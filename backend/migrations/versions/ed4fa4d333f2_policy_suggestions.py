"""Add policy_suggestions table.

Revision ID: ed4fa4d333f2
Revises: d5e6f7a8b9c0
Create Date: 2026-07-29

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "ed4fa4d333f2"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_email_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("experience_summary", sa.Text(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("intents", sa.JSON(), nullable=True),
        sa.Column("generalizable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("conflict_report", sa.JSON(), nullable=True),
        sa.Column("seen_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reviewed_by", sa.String(), nullable=True),
        sa.Column("reviewed_reason", sa.Text(), nullable=True),
        sa.Column("resulting_policy_key", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_policy_suggestions_source_email_id", "policy_suggestions", ["source_email_id"])
    op.create_index("ix_policy_suggestions_status", "policy_suggestions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_policy_suggestions_status", table_name="policy_suggestions")
    op.drop_index("ix_policy_suggestions_source_email_id", table_name="policy_suggestions")
    op.drop_table("policy_suggestions")
