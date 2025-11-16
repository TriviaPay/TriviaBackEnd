"""add new chat system tables

Revision ID: add_new_chat_system
Revises: f7g8h9i0j1k2
Create Date: 2024-12-20 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ENUM
from datetime import datetime

# revision identifiers, used by Alembic.
revision = 'add_new_chat_system'
down_revision = '3f58d87bad61'  # Current database state: add_wallet_balance_minor_column
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types (if they don't already exist)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE privatechatstatus AS ENUM ('pending', 'accepted', 'rejected');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE messagestatus AS ENUM ('sent', 'delivered', 'seen');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    
    # Global Chat Messages table
    op.create_table('global_chat_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('message', sa.String(), nullable=False),
        sa.Column('message_type', sa.String(), nullable=True, server_default='text'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_from_trivia_live', sa.Boolean(), nullable=True, server_default='false'),
        sa.Column('client_message_id', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_global_chat_messages_created_at', 'global_chat_messages', ['created_at'], unique=False)
    op.create_index('ix_global_chat_messages_user_id', 'global_chat_messages', ['user_id'], unique=False)
    op.create_index('ix_global_chat_messages_user_created', 'global_chat_messages', ['user_id', 'created_at'], unique=False)
    
    # Partial unique index for idempotency (only when client_message_id is not NULL)
    op.execute("""
        CREATE UNIQUE INDEX uq_global_chat_client_message_id 
        ON global_chat_messages (user_id, client_message_id)
        WHERE client_message_id IS NOT NULL
    """)
    
    # Private Chat Conversations table
    op.create_table('private_chat_conversations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user1_id', sa.BigInteger(), nullable=False),
        sa.Column('user2_id', sa.BigInteger(), nullable=False),
        sa.Column('status', ENUM('pending', 'accepted', 'rejected', name='privatechatstatus', create_type=False), nullable=False, server_default='pending'),
        sa.Column('requested_by', sa.BigInteger(), nullable=False),
        sa.Column('requested_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('responded_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('last_message_at', sa.DateTime(), nullable=True),
        sa.Column('last_read_message_id_user1', sa.Integer(), nullable=True),
        sa.Column('last_read_message_id_user2', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['user1_id'], ['users.account_id'], ),
        sa.ForeignKeyConstraint(['user2_id'], ['users.account_id'], ),
        sa.ForeignKeyConstraint(['requested_by'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user1_id', 'user2_id', name='uq_private_chat_users')
    )
    op.create_index('ix_private_chat_conversations_user1_id', 'private_chat_conversations', ['user1_id'], unique=False)
    op.create_index('ix_private_chat_conversations_user2_id', 'private_chat_conversations', ['user2_id'], unique=False)
    op.create_index('ix_private_chat_conversations_last_message_at', 'private_chat_conversations', ['last_message_at'], unique=False)
    
    # Private Chat Messages table
    op.create_table('private_chat_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('sender_id', sa.BigInteger(), nullable=False),
        sa.Column('message', sa.String(), nullable=False),
        sa.Column('status', ENUM('sent', 'delivered', 'seen', name='messagestatus', create_type=False), nullable=False, server_default='sent'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('delivered_at', sa.DateTime(), nullable=True),
        sa.Column('client_message_id', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['conversation_id'], ['private_chat_conversations.id'], ),
        sa.ForeignKeyConstraint(['sender_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_private_chat_messages_created_at', 'private_chat_messages', ['created_at'], unique=False)
    op.create_index('ix_private_chat_messages_conversation_id', 'private_chat_messages', ['conversation_id'], unique=False)
    op.create_index('ix_private_chat_messages_conv_created', 'private_chat_messages', ['conversation_id', 'created_at'], unique=False)
    
    # Partial unique index for idempotency
    op.execute("""
        CREATE UNIQUE INDEX uq_private_chat_client_message_id 
        ON private_chat_messages (conversation_id, sender_id, client_message_id)
        WHERE client_message_id IS NOT NULL
    """)
    
    # Trivia Live Chat Messages table
    op.create_table('trivia_live_chat_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('message', sa.String(), nullable=False),
        sa.Column('draw_date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('client_message_id', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_trivia_live_chat_messages_created_at', 'trivia_live_chat_messages', ['created_at'], unique=False)
    op.create_index('ix_trivia_live_chat_messages_draw_date', 'trivia_live_chat_messages', ['draw_date'], unique=False)
    op.create_index('ix_trivia_live_chat_messages_date_created', 'trivia_live_chat_messages', ['draw_date', 'created_at'], unique=False)
    
    # OneSignal Players table
    op.create_table('onesignal_players',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('player_id', sa.String(), nullable=False),
        sa.Column('platform', sa.String(), nullable=False),
        sa.Column('is_valid', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('last_active', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('last_failure_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('player_id')
    )
    op.create_index('ix_onesignal_players_user_id', 'onesignal_players', ['user_id'], unique=False)
    op.create_index('ix_onesignal_players_player_id', 'onesignal_players', ['player_id'], unique=False)


def downgrade() -> None:
    # Drop tables
    op.drop_table('onesignal_players')
    op.drop_table('trivia_live_chat_messages')
    op.drop_table('private_chat_messages')
    op.drop_table('private_chat_conversations')
    op.drop_table('global_chat_messages')
    
    # Drop enum types
    op.execute("DROP TYPE IF EXISTS messagestatus")
    op.execute("DROP TYPE IF EXISTS privatechatstatus")

