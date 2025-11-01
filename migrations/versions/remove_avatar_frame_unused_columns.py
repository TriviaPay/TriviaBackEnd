"""remove_avatar_frame_unused_columns

Revision ID: remove_unused_columns
Revises: 49e732d2545c
Create Date: 2025-11-01 05:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'remove_unused_columns'
down_revision: Union[str, None] = '49e732d2545c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop columns from avatars table
    op.drop_column('avatars', 'image_url')
    op.drop_column('avatars', 'size_bytes')
    op.drop_column('avatars', 'sha256')
    
    # Drop columns from frames table
    op.drop_column('frames', 'image_url')
    op.drop_column('frames', 'size_bytes')
    op.drop_column('frames', 'sha256')


def downgrade() -> None:
    # Re-add columns to avatars table
    op.add_column('avatars', sa.Column('image_url', sa.String(), nullable=True))
    op.add_column('avatars', sa.Column('size_bytes', sa.BigInteger(), nullable=True))
    op.add_column('avatars', sa.Column('sha256', sa.String(), nullable=True))
    
    # Re-add columns to frames table
    op.add_column('frames', sa.Column('image_url', sa.String(), nullable=True))
    op.add_column('frames', sa.Column('size_bytes', sa.BigInteger(), nullable=True))
    op.add_column('frames', sa.Column('sha256', sa.String(), nullable=True))

