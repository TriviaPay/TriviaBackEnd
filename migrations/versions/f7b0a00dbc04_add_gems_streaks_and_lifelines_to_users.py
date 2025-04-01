"""add_gems_streaks_and_lifelines_to_users

Revision ID: f7b0a00dbc04
Revises: 3f451daf1184
Create Date: 2025-03-29 21:43:38.535

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7b0a00dbc04'
down_revision: Union[str, None] = '3f451daf1184'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add gems column with default value 0
    op.add_column('users', sa.Column('gems', sa.Integer(), nullable=False, server_default='0'))
    
    # Add streaks column with default value 0
    op.add_column('users', sa.Column('streaks', sa.Integer(), nullable=False, server_default='0'))
    
    # Add lifeline_changes_remaining column with default value 3
    op.add_column('users', sa.Column('lifeline_changes_remaining', sa.Integer(), nullable=False, server_default='3'))
    
    # Add last_streak_date column as nullable timestamp
    op.add_column('users', sa.Column('last_streak_date', sa.DateTime(), nullable=True))


def downgrade() -> None:
    # Remove all added columns
    op.drop_column('users', 'last_streak_date')
    op.drop_column('users', 'lifeline_changes_remaining')
    op.drop_column('users', 'streaks')
    op.drop_column('users', 'gems')
