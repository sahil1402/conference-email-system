"""policy lineage: supersedes / superseded_by / root_key / version

Adds the versioning/lineage columns to policy_documents for chair edit-a-copy.
Additive only. Existing rows: supersedes/superseded_by/root_key stay NULL (each
is its own lineage root), version defaults to 1 via server_default.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-21 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("policy_documents", sa.Column("supersedes", sa.String(length=128), nullable=True))
    op.add_column("policy_documents", sa.Column("superseded_by", sa.String(length=128), nullable=True))
    op.add_column("policy_documents", sa.Column("root_key", sa.String(length=128), nullable=True))
    op.add_column(
        "policy_documents",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_policy_documents_supersedes", "policy_documents", ["supersedes"])
    op.create_index("ix_policy_documents_superseded_by", "policy_documents", ["superseded_by"])
    op.create_index("ix_policy_documents_root_key", "policy_documents", ["root_key"])


def downgrade() -> None:
    op.drop_index("ix_policy_documents_root_key", table_name="policy_documents")
    op.drop_index("ix_policy_documents_superseded_by", table_name="policy_documents")
    op.drop_index("ix_policy_documents_supersedes", table_name="policy_documents")
    op.drop_column("policy_documents", "version")
    op.drop_column("policy_documents", "root_key")
    op.drop_column("policy_documents", "superseded_by")
    op.drop_column("policy_documents", "supersedes")
