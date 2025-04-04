"""merge badge_columns and question_done

Revision ID: 7f1dd52e9cbd
Revises: 4b1348813a82, badge_columns
Create Date: 2025-04-03 20:14:25.954319

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f1dd52e9cbd'
down_revision: Union[str, None] = ('4b1348813a82', 'badge_columns')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
