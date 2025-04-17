"""fix_migration_chain

Revision ID: 18afb0fdcf90
Revises: 919cf2565760
Create Date: 2025-04-16 00:42:28.674915

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '18afb0fdcf90'
down_revision: Union[str, None] = '919cf2565760'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
