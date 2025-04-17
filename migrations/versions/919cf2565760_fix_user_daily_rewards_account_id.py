"""fix_user_daily_rewards_account_id

Revision ID: 919cf2565760
Revises: aa484a3e08f6
Create Date: 2025-04-16 00:41:16.302978

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '919cf2565760'
down_revision: Union[str, None] = 'aa484a3e08f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
