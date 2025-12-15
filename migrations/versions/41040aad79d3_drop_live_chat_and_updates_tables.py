"""drop_live_chat_and_updates_tables

Revision ID: 41040aad79d3
Revises: drop_unused_z_tables
Create Date: 2025-12-15 01:11:03.902420

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '41040aad79d3'
down_revision: Union[str, None] = 'drop_unused_z_tables'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop tables in order to handle foreign key constraints
    # Drop child tables first, then parent tables
    
    # Live Chat tables (drop children first)
    op.drop_table('live_chat_likes')
    op.drop_table('live_chat_viewers')
    op.drop_table('live_chat_messages')
    op.drop_table('live_chat_sessions')
    
    # Live Updates table
    op.drop_table('liveupdates')


def downgrade() -> None:
    # Note: This downgrade would require recreating all the tables with their full schema
    # For now, we'll just document that this is a destructive migration
    # In production, you would need to restore from backup if you need to rollback
    raise NotImplementedError("This migration is destructive and cannot be automatically rolled back. Restore from backup if needed.")
