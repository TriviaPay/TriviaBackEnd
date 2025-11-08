"""add_user_daily_rewards_table

Revision ID: b7c8d9e0f1a2
Revises: 49e732d2545c
Create Date: 2025-01-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, None] = 'remove_badge_image_url'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create user_daily_rewards table
    op.create_table(
        'user_daily_rewards',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('week_start_date', sa.Date(), nullable=False),
        sa.Column('day1_status', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('day2_status', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('day3_status', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('day4_status', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('day5_status', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('day6_status', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('day7_status', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_id', 'week_start_date', name='uq_user_week_rewards')
    )
    
    # Create index on account_id for faster lookups
    op.create_index('ix_user_daily_rewards_account_id', 'user_daily_rewards', ['account_id'])


def downgrade() -> None:
    op.drop_index('ix_user_daily_rewards_account_id', table_name='user_daily_rewards')
    op.drop_table('user_daily_rewards')

