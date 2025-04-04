"""add_badge_to_user

Revision ID: 4b1348813a82
Revises: 58355ef047f4
Create Date: 2024-03-30 21:14:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4b1348813a82'
down_revision: Union[str, None] = '58355ef047f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Column already exists, so we skip adding it
    pass


def downgrade() -> None:
    # Since we're not adding the column in upgrade, we don't need to remove it in downgrade
    pass
