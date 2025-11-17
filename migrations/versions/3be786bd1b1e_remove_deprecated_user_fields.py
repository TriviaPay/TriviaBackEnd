"""remove_deprecated_user_fields

Revision ID: 3be786bd1b1e
Revises: 9cc2e1103ee5
Create Date: 2025-11-17 01:15:46.161323

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3be786bd1b1e'
down_revision: Union[str, None] = '9cc2e1103ee5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove deprecated user fields
    op.drop_column('users', 'streaks')
    op.drop_column('users', 'lifeline_changes_remaining')
    op.drop_column('users', 'last_streak_date')
    op.drop_column('users', 'owned_boosts')


def downgrade() -> None:
    # Restore deprecated user fields
    op.add_column('users', sa.Column('streaks', sa.Integer(), nullable=True, server_default='0'))
    op.add_column('users', sa.Column('lifeline_changes_remaining', sa.Integer(), nullable=True, server_default='3'))
    op.add_column('users', sa.Column('last_streak_date', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('owned_boosts', sa.Text(), nullable=True))
