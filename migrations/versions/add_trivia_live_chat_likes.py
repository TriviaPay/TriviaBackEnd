"""add trivia live chat likes table

Revision ID: add_trivia_likes
Revises: add_chat_viewer_tables
Create Date: 2024-11-20 01:10:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_trivia_likes'
down_revision = 'add_chat_viewer_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Trivia Live Chat Likes table
    op.create_table('trivia_live_chat_likes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('draw_date', sa.Date(), nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.ForeignKeyConstraint(['message_id'], ['trivia_live_chat_messages.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_trivia_live_chat_likes_user_id', 'trivia_live_chat_likes', ['user_id'], unique=False)
    op.create_index('ix_trivia_live_chat_likes_draw_date', 'trivia_live_chat_likes', ['draw_date'], unique=False)
    op.create_unique_constraint('uq_trivia_live_chat_like_user_draw_message', 'trivia_live_chat_likes', ['user_id', 'draw_date', 'message_id'])


def downgrade() -> None:
    op.drop_table('trivia_live_chat_likes')

