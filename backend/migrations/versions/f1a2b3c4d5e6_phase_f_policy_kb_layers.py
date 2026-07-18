"""phase_f_policy_kb_layers

Adds ``visibility`` (public|internal) and ``status`` (active|inactive) to
``policy_documents`` for the layered KB. Server defaults backfill every existing
row to public/active. Schema only.

Revision ID: f1a2b3c4d5e6
Revises: b8d3f6a1c204
Create Date: 2026-07-18 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "b8d3f6a1c204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("policy_documents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("visibility", sa.String(length=16), nullable=False, server_default="public")
        )
        batch_op.add_column(
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active")
        )
        batch_op.create_index("ix_policy_documents_visibility", ["visibility"])
        batch_op.create_index("ix_policy_documents_status", ["status"])


def downgrade() -> None:
    with op.batch_alter_table("policy_documents", schema=None) as batch_op:
        batch_op.drop_index("ix_policy_documents_status")
        batch_op.drop_index("ix_policy_documents_visibility")
        batch_op.drop_column("status")
        batch_op.drop_column("visibility")
