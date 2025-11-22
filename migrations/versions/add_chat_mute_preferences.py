"""add chat mute preferences table

Revision ID: add_chat_mute_preferences
Revises: remove_is_from_trivia_live
Create Date: 2024-12-21 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from datetime import datetime

# revision identifiers, used by Alembic.
revision = 'add_chat_mute_preferences'
down_revision = 'remove_is_from_trivia_live'  # Latest migration
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Chat Mute Preferences table
    op.create_table('chat_mute_preferences',
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('global_chat_muted', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('trivia_live_chat_muted', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('private_chat_muted_users', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('user_id')
    )
    op.create_index('ix_chat_mute_preferences_user_id', 'chat_mute_preferences', ['user_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_chat_mute_preferences_user_id', table_name='chat_mute_preferences')
    op.drop_table('chat_mute_preferences')

