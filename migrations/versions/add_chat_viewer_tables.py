"""add chat viewer tables

Revision ID: add_chat_viewer_tables
Revises: add_new_chat_system
Create Date: 2024-11-20 00:50:26.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from datetime import datetime

# revision identifiers, used by Alembic.
revision = 'add_chat_viewer_tables'
down_revision = '3be786bd1b1e'  # Current head: remove_deprecated_user_fields
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Global Chat Viewers table
    op.create_table('global_chat_viewers',
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('last_seen', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('user_id')
    )
    op.create_index('ix_global_chat_viewers_last_seen', 'global_chat_viewers', ['last_seen'], unique=False)
    
    # Trivia Live Chat Viewers table
    op.create_table('trivia_live_chat_viewers',
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('draw_date', sa.Date(), nullable=False),
        sa.Column('last_seen', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('user_id', 'draw_date')
    )
    op.create_index('ix_trivia_live_chat_viewers_draw_date', 'trivia_live_chat_viewers', ['draw_date'], unique=False)
    op.create_index('ix_trivia_live_chat_viewers_last_seen', 'trivia_live_chat_viewers', ['last_seen'], unique=False)
    op.create_unique_constraint('uq_trivia_live_chat_viewer_user_draw', 'trivia_live_chat_viewers', ['user_id', 'draw_date'])


def downgrade() -> None:
    op.drop_table('trivia_live_chat_viewers')
    op.drop_table('global_chat_viewers')

