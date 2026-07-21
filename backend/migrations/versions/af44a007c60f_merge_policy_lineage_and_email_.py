"""merge policy-lineage and email-processing-results branches

Revision ID: af44a007c60f
Revises: c3d4e5f6a7b8, e5f6a7b8c9d0
Create Date: 2026-07-21 07:37:08.194720

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'af44a007c60f'
down_revision: Union[str, Sequence[str], None] = ('c3d4e5f6a7b8', 'e5f6a7b8c9d0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
