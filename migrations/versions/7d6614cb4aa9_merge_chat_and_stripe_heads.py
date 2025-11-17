"""merge_chat_and_stripe_heads

Revision ID: 7d6614cb4aa9
Revises: add_new_chat_system, b2c3d4e5f6a8
Create Date: 2025-11-16 15:34:52.466660

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7d6614cb4aa9'
down_revision: Union[str, None] = ('add_new_chat_system', 'b2c3d4e5f6a8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
