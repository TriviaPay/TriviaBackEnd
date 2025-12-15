"""drop unused z_ tables

Revision ID: drop_unused_z_tables
Revises: rename_z_blocks_presence
Create Date: 2025-01-XX 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'drop_unused_z_tables'
down_revision: Union[str, None] = 'rename_z_blocks_presence'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop tables in order to handle foreign key constraints
    # Drop child tables first, then parent tables
    
    # Group-related tables (drop children first)
    op.drop_table('z_group_delivery')
    op.drop_table('z_group_messages')
    op.drop_table('z_group_sender_keys')
    op.drop_table('z_group_bans')
    op.drop_table('z_group_invites')
    op.drop_table('z_group_participants')
    op.drop_table('z_groups')
    
    # Status-related tables (drop children first)
    op.drop_table('z_status_views')
    op.drop_table('z_status_audience')
    op.drop_table('z_status_posts')
    
    # DM-related tables (drop children first)
    op.drop_table('z_dm_delivery')
    op.drop_table('z_dm_messages')
    op.drop_table('z_dm_participants')
    op.drop_table('z_dm_conversations')
    
    # E2EE-related tables (drop children first)
    op.drop_table('z_e2ee_one_time_prekeys')
    op.drop_table('z_e2ee_key_bundles')
    op.drop_table('z_e2ee_devices')
    
    # Supporting tables
    op.drop_table('z_device_revocations')


def downgrade() -> None:
    # Note: This downgrade would require recreating all the tables with their full schema
    # For now, we'll just document that this is a destructive migration
    # In production, you would need to restore from backup if you need to rollback
    raise NotImplementedError("This migration is destructive and cannot be automatically rolled back. Restore from backup if needed.")
