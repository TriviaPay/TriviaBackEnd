"""add reply_to_message_id to chat message tables

Revision ID: add_reply_to_message
Revises: 
Create Date: 2025-11-29

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_reply_to_message'
down_revision = '7452116ea361'  # Latest migration
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add reply_to_message_id to private_chat_messages
    op.add_column('private_chat_messages', 
        sa.Column('reply_to_message_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_private_chat_messages_reply_to',
        'private_chat_messages', 'private_chat_messages',
        ['reply_to_message_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_index('ix_private_chat_messages_reply_to', 'private_chat_messages', ['reply_to_message_id'])
    
    # Add reply_to_message_id to global_chat_messages
    op.add_column('global_chat_messages',
        sa.Column('reply_to_message_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_global_chat_messages_reply_to',
        'global_chat_messages', 'global_chat_messages',
        ['reply_to_message_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_index('ix_global_chat_messages_reply_to', 'global_chat_messages', ['reply_to_message_id'])
    
    # Add reply_to_message_id to trivia_live_chat_messages
    op.add_column('trivia_live_chat_messages',
        sa.Column('reply_to_message_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_trivia_live_chat_messages_reply_to',
        'trivia_live_chat_messages', 'trivia_live_chat_messages',
        ['reply_to_message_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_index('ix_trivia_live_chat_messages_reply_to', 'trivia_live_chat_messages', ['reply_to_message_id'])
    
    # Add reply_to_message_id to z_group_messages (UUID type)
    op.add_column('z_group_messages',
        sa.Column('reply_to_message_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_group_messages_reply_to',
        'z_group_messages', 'z_group_messages',
        ['reply_to_message_id'], ['id'],
        ondelete='SET NULL'
    )
    op.create_index('ix_group_messages_reply_to', 'z_group_messages', ['reply_to_message_id'])


def downgrade() -> None:
    # Remove indexes and foreign keys first
    op.drop_index('ix_group_messages_reply_to', table_name='z_group_messages')
    op.drop_constraint('fk_group_messages_reply_to', 'z_group_messages', type_='foreignkey')
    op.drop_column('z_group_messages', 'reply_to_message_id')
    
    op.drop_index('ix_trivia_live_chat_messages_reply_to', table_name='trivia_live_chat_messages')
    op.drop_constraint('fk_trivia_live_chat_messages_reply_to', 'trivia_live_chat_messages', type_='foreignkey')
    op.drop_column('trivia_live_chat_messages', 'reply_to_message_id')
    
    op.drop_index('ix_global_chat_messages_reply_to', table_name='global_chat_messages')
    op.drop_constraint('fk_global_chat_messages_reply_to', 'global_chat_messages', type_='foreignkey')
    op.drop_column('global_chat_messages', 'reply_to_message_id')
    
    op.drop_index('ix_private_chat_messages_reply_to', table_name='private_chat_messages')
    op.drop_constraint('fk_private_chat_messages_reply_to', 'private_chat_messages', type_='foreignkey')
    op.drop_column('private_chat_messages', 'reply_to_message_id')

