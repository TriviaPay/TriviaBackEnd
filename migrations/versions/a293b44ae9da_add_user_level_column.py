"""add_user_level_column

Revision ID: a293b44ae9da
Revises: 6808d7879c60
Create Date: 2025-12-13 15:56:01.529365

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a293b44ae9da'
down_revision: Union[str, None] = '6808d7879c60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add level column to users table with default value of 1
    op.add_column('users', sa.Column('level', sa.Integer(), nullable=False, server_default='1'))


def downgrade() -> None:
    # Remove level column from users table
    op.drop_column('users', 'level')
