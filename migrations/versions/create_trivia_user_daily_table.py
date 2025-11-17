"""create_trivia_user_daily_table

Revision ID: c1d2e3f4a5b6
Revises: remove_avatar_frame_unused_columns
Create Date: 2025-11-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = 'remove_unused_columns'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create trivia_user_daily table
    op.create_table(
        'trivia_user_daily',
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('question_order', sa.Integer(), nullable=False),
        sa.Column('question_number', sa.Integer(), nullable=False),
        sa.Column('unlock_method', sa.String(), nullable=True),
        sa.Column('viewed_at', sa.DateTime(), nullable=True),
        sa.Column('user_answer', sa.String(), nullable=True),
        sa.Column('is_correct', sa.Boolean(), nullable=True),
        sa.Column('answered_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='locked'),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['account_id'], ['users.account_id'], ),
        sa.ForeignKeyConstraint(['question_number'], ['trivia.question_number'], ),
        sa.PrimaryKeyConstraint('account_id', 'date', 'question_order'),
        sa.UniqueConstraint('account_id', 'date', 'question_order', name='uq_user_daily_question')
    )
    
    # Create indexes
    op.create_index('ix_trivia_user_daily_account_date', 'trivia_user_daily', ['account_id', 'date'])


def downgrade() -> None:
    op.drop_index('ix_trivia_user_daily_account_date', table_name='trivia_user_daily')
    op.drop_table('trivia_user_daily')

