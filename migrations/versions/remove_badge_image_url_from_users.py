"""remove_badge_image_url_from_users

Revision ID: remove_badge_image_url
Revises: c3d4e5f6a7b8
Create Date: 2025-11-02 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'remove_badge_image_url'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if column exists before dropping (for safety)
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    columns = [col['name'] for col in inspector.get_columns('users')]
    
    if 'badge_image_url' in columns:
        # Drop badge_image_url column from users table
        # Badge URLs should be retrieved from badges table using badge_id
        op.drop_column('users', 'badge_image_url')


def downgrade() -> None:
    # Re-add badge_image_url column to users table
    op.add_column('users', sa.Column('badge_image_url', sa.String(), nullable=True))

