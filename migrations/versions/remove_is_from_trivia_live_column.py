"""remove is_from_trivia_live column

Revision ID: remove_is_from_trivia_live
Revises: add_trivia_likes
Create Date: 2024-11-20 02:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'remove_is_from_trivia_live'
down_revision = 'add_trivia_likes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop is_from_trivia_live column from global_chat_messages table
    op.drop_column('global_chat_messages', 'is_from_trivia_live')


def downgrade() -> None:
    # Re-add the column if needed to rollback
    op.add_column('global_chat_messages',
        sa.Column('is_from_trivia_live', sa.Boolean(), nullable=True, server_default='false')
    )

