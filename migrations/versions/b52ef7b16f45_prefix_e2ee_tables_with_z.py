"""prefix_e2ee_tables_with_z

Revision ID: b52ef7b16f45
Revises: 316bb2be282d
Create Date: 2025-11-16 23:26:48.695081

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b52ef7b16f45'
down_revision: Union[str, None] = '316bb2be282d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename all E2EE-related tables with z_ prefix
    # E2EE Core Tables
    op.rename_table('e2ee_devices', 'z_e2ee_devices')
    op.rename_table('e2ee_key_bundles', 'z_e2ee_key_bundles')
    op.rename_table('e2ee_one_time_prekeys', 'z_e2ee_one_time_prekeys')
    
    # DM Tables
    op.rename_table('dm_conversations', 'z_dm_conversations')
    op.rename_table('dm_participants', 'z_dm_participants')
    op.rename_table('dm_messages', 'z_dm_messages')
    op.rename_table('dm_delivery', 'z_dm_delivery')
    
    # Groups Tables
    op.rename_table('groups', 'z_groups')
    op.rename_table('group_participants', 'z_group_participants')
    op.rename_table('group_messages', 'z_group_messages')
    op.rename_table('group_delivery', 'z_group_delivery')
    op.rename_table('group_sender_keys', 'z_group_sender_keys')
    op.rename_table('group_invites', 'z_group_invites')
    op.rename_table('group_bans', 'z_group_bans')
    
    # Status Tables
    op.rename_table('status_posts', 'z_status_posts')
    op.rename_table('status_audience', 'z_status_audience')
    op.rename_table('status_views', 'z_status_views')
    
    # Presence Table
    op.rename_table('user_presence', 'z_user_presence')
    
    # Supporting Tables
    op.rename_table('blocks', 'z_blocks')
    op.rename_table('device_revocations', 'z_device_revocations')


def downgrade() -> None:
    # Reverse the renaming - remove z_ prefix
    # Supporting Tables
    op.rename_table('z_device_revocations', 'device_revocations')
    op.rename_table('z_blocks', 'blocks')
    
    # Presence Table
    op.rename_table('z_user_presence', 'user_presence')
    
    # Status Tables
    op.rename_table('z_status_views', 'status_views')
    op.rename_table('z_status_audience', 'status_audience')
    op.rename_table('z_status_posts', 'status_posts')
    
    # Groups Tables
    op.rename_table('z_group_bans', 'group_bans')
    op.rename_table('z_group_invites', 'group_invites')
    op.rename_table('z_group_sender_keys', 'group_sender_keys')
    op.rename_table('z_group_delivery', 'group_delivery')
    op.rename_table('z_group_messages', 'group_messages')
    op.rename_table('z_group_participants', 'group_participants')
    op.rename_table('z_groups', 'groups')
    
    # DM Tables
    op.rename_table('z_dm_delivery', 'dm_delivery')
    op.rename_table('z_dm_messages', 'dm_messages')
    op.rename_table('z_dm_participants', 'dm_participants')
    op.rename_table('z_dm_conversations', 'dm_conversations')
    
    # E2EE Core Tables
    op.rename_table('z_e2ee_one_time_prekeys', 'e2ee_one_time_prekeys')
    op.rename_table('z_e2ee_key_bundles', 'e2ee_key_bundles')
    op.rename_table('z_e2ee_devices', 'e2ee_devices')
