"""add_e2ee_tables_from_branch

Revision ID: 71a88496debd
Revises: f7g8h9i0j1k2
Create Date: 2025-11-09 16:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '71a88496debd'
down_revision: Union[str, None] = 'f7g8h9i0j1k2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # E2EE Devices table
    op.create_table('e2ee_devices',
        sa.Column('device_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('device_name', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='active'),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('device_id')
    )
    op.create_index(op.f('ix_e2ee_devices_device_id'), 'e2ee_devices', ['device_id'], unique=False)
    op.create_index('ix_e2ee_devices_user_id', 'e2ee_devices', ['user_id'], unique=False)

    # E2EE Key Bundles table
    op.create_table('e2ee_key_bundles',
        sa.Column('device_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('identity_key_pub', sa.String(), nullable=False),
        sa.Column('signed_prekey_pub', sa.String(), nullable=False),
        sa.Column('signed_prekey_sig', sa.String(), nullable=False),
        sa.Column('prekeys_remaining', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('bundle_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['device_id'], ['e2ee_devices.device_id'], ),
        sa.PrimaryKeyConstraint('device_id'),
        sa.UniqueConstraint('device_id')
    )

    # E2EE One-Time Prekeys table
    op.create_table('e2ee_one_time_prekeys',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('prekey_pub', sa.String(), nullable=False),
        sa.Column('claimed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['device_id'], ['e2ee_devices.device_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_e2ee_one_time_prekeys_device_claimed', 'e2ee_one_time_prekeys', ['device_id', 'claimed'], unique=False)

    # DM Conversations table
    op.create_table('dm_conversations',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('last_message_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('sealed_sender_enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_dm_conversations_last_message_at', 'dm_conversations', ['last_message_at'], unique=False)

    # DM Participants table
    op.create_table('dm_participants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('device_ids', postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(['conversation_id'], ['dm_conversations.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversation_id', 'user_id', name='uq_dm_participants_conversation_user')
    )
    op.create_index('ix_dm_participants_conversation_id', 'dm_participants', ['conversation_id'], unique=False)
    op.create_index('ix_dm_participants_user_id', 'dm_participants', ['user_id'], unique=False)

    # DM Messages table
    op.create_table('dm_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('sender_user_id', sa.BigInteger(), nullable=False),
        sa.Column('sender_device_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('ciphertext', sa.LargeBinary(), nullable=False),
        sa.Column('proto', sa.SmallInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('client_message_id', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['conversation_id'], ['dm_conversations.id'], ),
        sa.ForeignKeyConstraint(['sender_user_id'], ['users.account_id'], ),
        sa.ForeignKeyConstraint(['sender_device_id'], ['e2ee_devices.device_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('client_message_id')
    )
    op.create_index('ix_dm_messages_conversation_created', 'dm_messages', ['conversation_id', 'created_at', 'id'], unique=False)
    op.create_index('ix_dm_messages_created_at', 'dm_messages', ['created_at'], unique=False)

    # DM Delivery table
    op.create_table('dm_delivery',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('message_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('recipient_user_id', sa.BigInteger(), nullable=False),
        sa.Column('delivered_at', sa.DateTime(), nullable=True),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['message_id'], ['dm_messages.id'], ),
        sa.ForeignKeyConstraint(['recipient_user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('message_id', 'recipient_user_id', name='uq_dm_delivery_message_recipient')
    )
    op.create_index('ix_dm_delivery_recipient_read', 'dm_delivery', ['recipient_user_id', 'read_at'], unique=False)

    # Blocks table
    op.create_table('blocks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('blocker_id', sa.BigInteger(), nullable=False),
        sa.Column('blocked_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['blocker_id'], ['users.account_id'], ),
        sa.ForeignKeyConstraint(['blocked_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('blocker_id', 'blocked_id', name='uq_blocks_blocker_blocked')
    )
    op.create_index('ix_blocks_blocker_id', 'blocks', ['blocker_id'], unique=False)
    op.create_index('ix_blocks_blocked_id', 'blocks', ['blocked_id'], unique=False)

    # Device Revocations table
    op.create_table('device_revocations',
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('device_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('reason', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('user_id', 'device_id')
    )


def downgrade() -> None:
    op.drop_table('device_revocations')
    op.drop_table('blocks')
    op.drop_table('dm_delivery')
    op.drop_table('dm_messages')
    op.drop_table('dm_participants')
    op.drop_table('dm_conversations')
    op.drop_table('e2ee_one_time_prekeys')
    op.drop_table('e2ee_key_bundles')
    op.drop_table('e2ee_devices')
