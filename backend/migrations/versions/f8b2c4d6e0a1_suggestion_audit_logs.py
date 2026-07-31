"""Add suggestion_audit_logs table.

Append-only history of chair accept/reject actions on CEL policy suggestions.
``policy_suggestions.reviewed_by`` / ``reviewed_reason`` hold only current state
and are overwritten on each action; this table keeps every one.

``suggestion_id`` intentionally carries NO foreign key — SQLite (the default for
local dev, tests and CI) does not enforce FKs without ``PRAGMA foreign_keys=ON``,
which nothing here sets, so an FK would be a Postgres-only guarantee that the
test suite could never exercise. Matches the existing convention on
``policy_audit_logs.policy_key`` and ``policy_suggestions.source_email_id``.

Revision ID: f8b2c4d6e0a1
Revises: ed4fa4d333f2
Create Date: 2026-07-30

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f8b2c4d6e0a1"
down_revision = "ed4fa4d333f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suggestion_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("suggestion_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("resulting_policy_key", sa.String(length=128), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_suggestion_audit_logs_suggestion_id",
        "suggestion_audit_logs",
        ["suggestion_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_suggestion_audit_logs_suggestion_id",
        table_name="suggestion_audit_logs",
    )
    op.drop_table("suggestion_audit_logs")
