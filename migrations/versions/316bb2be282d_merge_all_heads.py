"""merge_all_heads

Revision ID: 316bb2be282d
Revises: 7d6614cb4aa9, a1b2c3d4e5f6
Create Date: 2025-11-16 15:35:08.709359

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '316bb2be282d'
down_revision: Union[str, None] = ('7d6614cb4aa9', 'a1b2c3d4e5f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
