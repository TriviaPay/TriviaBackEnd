"""rename z_blocks and z_user_presence back to blocks and user_presence

Revision ID: rename_z_blocks_presence
Revises: add_notifications_table
Create Date: 2025-01-XX 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'rename_z_blocks_presence'
down_revision: Union[str, None] = 'add_notifications_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename z_blocks back to blocks (used by private chat)
    op.rename_table('z_blocks', 'blocks')
    
    # Rename z_user_presence back to user_presence (used by private chat)
    op.rename_table('z_user_presence', 'user_presence')


def downgrade() -> None:
    # Reverse the renaming - add z_ prefix back
    op.rename_table('user_presence', 'z_user_presence')
    op.rename_table('blocks', 'z_blocks')
