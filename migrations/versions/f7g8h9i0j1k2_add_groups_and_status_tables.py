"""add groups and status tables

Revision ID: f7g8h9i0j1k2
Revises: a1b2c3d4e5f6
Create Date: 2024-01-15 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from datetime import datetime
from alembic import context

# revision identifiers, used by Alembic.
revision = 'f7g8h9i0j1k2'
down_revision = 'b7c8d9e0f1a2'  # Current database state
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Groups table
    op.create_table('groups',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('about', sa.String(), nullable=True),
        sa.Column('photo_url', sa.String(), nullable=True),
        sa.Column('created_by', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('max_participants', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('group_epoch', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_closed', sa.Boolean(), nullable=False, server_default='false'),
        sa.ForeignKeyConstraint(['created_by'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_groups_created_by', 'groups', ['created_by'], unique=False)
    op.create_index('ix_groups_group_epoch', 'groups', ['group_epoch'], unique=False)

    # Group Participants table
    op.create_table('group_participants',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('group_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('role', sa.Enum('owner', 'admin', 'member', name='grouprole'), nullable=False, server_default='member'),
        sa.Column('joined_at', sa.DateTime(), nullable=True),
        sa.Column('mute_until', sa.DateTime(), nullable=True),
        sa.Column('is_banned', sa.Boolean(), nullable=False, server_default='false'),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('group_id', 'user_id', name='uq_group_participants_group_user')
    )
    op.create_index('ix_group_participants_group_id', 'group_participants', ['group_id'], unique=False)
    op.create_index('ix_group_participants_user_id', 'group_participants', ['user_id'], unique=False)
    op.create_index('ix_group_participants_user_group', 'group_participants', ['user_id', 'group_id'], unique=False)

    # Group Messages table
    # Check if e2ee_devices table exists before creating foreign key
    from alembic import context
    conn = context.get_bind()
    inspector = sa.inspect(conn)
    e2ee_devices_exists = 'e2ee_devices' in inspector.get_table_names()
    
    group_messages_table = op.create_table('group_messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('group_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('sender_user_id', sa.BigInteger(), nullable=False),
        sa.Column('sender_device_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('ciphertext', sa.LargeBinary(), nullable=False),
        sa.Column('proto', sa.SmallInteger(), nullable=False),  # 10=sender-key msg, 11=sender-key distribution
        sa.Column('group_epoch', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('client_message_id', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
        sa.ForeignKeyConstraint(['sender_user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Add foreign key to e2ee_devices only if table exists
    if e2ee_devices_exists:
        op.create_foreign_key(
            'fk_group_messages_sender_device',
            'group_messages',
            'e2ee_devices',
            ['sender_device_id'],
            ['device_id']
        )
    op.create_index('ix_group_messages_group_created', 'group_messages', ['group_id', 'created_at', 'id'], unique=False)
    op.create_index('ix_group_messages_created_at', 'group_messages', ['created_at'], unique=False)
    op.create_index('ix_group_messages_client_id', 'group_messages', ['client_message_id'], unique=True)

    # Group Delivery table
    op.create_table('group_delivery',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('message_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('recipient_user_id', sa.BigInteger(), nullable=False),
        sa.Column('delivered_at', sa.DateTime(), nullable=True),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['message_id'], ['group_messages.id'], ),
        sa.ForeignKeyConstraint(['recipient_user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('message_id', 'recipient_user_id', name='uq_group_delivery_message_recipient')
    )
    op.create_index('ix_group_delivery_recipient_read', 'group_delivery', ['recipient_user_id', 'read_at'], unique=False)

    # Group Sender Keys table (metadata only)
    group_sender_keys_table = op.create_table('group_sender_keys',
        sa.Column('group_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('sender_user_id', sa.BigInteger(), nullable=False),
        sa.Column('sender_device_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('group_epoch', sa.Integer(), nullable=False),
        sa.Column('sender_key_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('current_chain_index', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('rotated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
        sa.ForeignKeyConstraint(['sender_user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('group_id', 'sender_user_id', 'sender_device_id', 'group_epoch')
    )
    
    # Add foreign key to e2ee_devices only if table exists
    if e2ee_devices_exists:
        op.create_foreign_key(
            'fk_group_sender_keys_device',
            'group_sender_keys',
            'e2ee_devices',
            ['sender_device_id'],
            ['device_id']
        )
    op.create_index('ix_group_sender_keys_lookup', 'group_sender_keys', ['group_id', 'sender_user_id', 'sender_device_id', 'group_epoch'], unique=False)

    # Group Invites table
    op.create_table('group_invites',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('group_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_by', sa.BigInteger(), nullable=False),
        sa.Column('type', sa.Enum('link', 'direct', name='invitetype'), nullable=False),
        sa.Column('code', sa.String(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('max_uses', sa.Integer(), nullable=True),
        sa.Column('uses', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_group_invites_code', 'group_invites', ['code'], unique=True)
    op.create_index('ix_group_invites_group_id', 'group_invites', ['group_id'], unique=False)

    # Group Bans table
    op.create_table('group_bans',
        sa.Column('group_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('banned_by', sa.BigInteger(), nullable=False),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('banned_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['group_id'], ['groups.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.ForeignKeyConstraint(['banned_by'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('group_id', 'user_id')
    )
    op.create_index('ix_group_bans_user_id', 'group_bans', ['user_id'], unique=False)

    # Status Posts table
    op.create_table('status_posts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('owner_user_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('media_meta', postgresql.JSONB(), nullable=True),
        sa.Column('audience_mode', sa.Enum('contacts', 'custom', name='audiencemode'), nullable=False, server_default='contacts'),
        sa.Column('post_epoch', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_status_posts_owner_expires', 'status_posts', ['owner_user_id', 'expires_at', 'created_at'], unique=False)
    op.create_index('ix_status_posts_expires_at', 'status_posts', ['expires_at'], unique=False)

    # Status Audience table
    op.create_table('status_audience',
        sa.Column('post_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('viewer_user_id', sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(['post_id'], ['status_posts.id'], ),
        sa.ForeignKeyConstraint(['viewer_user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('post_id', 'viewer_user_id')
    )
    op.create_index('ix_status_audience_viewer', 'status_audience', ['viewer_user_id'], unique=False)

    # Status Views table
    op.create_table('status_views',
        sa.Column('post_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('viewer_user_id', sa.BigInteger(), nullable=False),
        sa.Column('viewed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['post_id'], ['status_posts.id'], ),
        sa.ForeignKeyConstraint(['viewer_user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('post_id', 'viewer_user_id')
    )
    op.create_index('ix_status_views_viewer', 'status_views', ['viewer_user_id'], unique=False)

    # User Presence table
    op.create_table('user_presence',
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('device_online', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('privacy_settings', postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('user_id')
    )


def downgrade() -> None:
    op.drop_table('user_presence')
    op.drop_table('status_views')
    op.drop_table('status_audience')
    op.drop_table('status_posts')
    op.drop_table('group_bans')
    op.drop_table('group_invites')
    op.drop_table('group_sender_keys')
    op.drop_table('group_delivery')
    op.drop_table('group_messages')
    op.drop_table('group_participants')
    op.drop_table('groups')
    op.execute("DROP TYPE IF EXISTS grouprole")
    op.execute("DROP TYPE IF EXISTS invitetype")
    op.execute("DROP TYPE IF EXISTS audiencemode")

