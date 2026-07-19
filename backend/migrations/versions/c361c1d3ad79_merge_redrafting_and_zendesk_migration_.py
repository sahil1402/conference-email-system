"""merge redrafting and zendesk migration heads

Revision ID: c361c1d3ad79
Revises: c1d2e3f4a5b6, e3f5a7c9b1d2
Create Date: 2026-07-19 15:43:36.148439

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c361c1d3ad79'
down_revision: Union[str, Sequence[str], None] = ('c1d2e3f4a5b6', 'e3f5a7c9b1d2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
