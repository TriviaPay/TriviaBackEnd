"""add_badge_columns_only

Revision ID: badge_columns
Revises: a093e820d8c1
Create Date: 2025-03-31 23:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'badge_columns'
down_revision = 'a093e820d8c1'
branch_labels = None
depends_on = None


def upgrade():
    # Add badge columns to users table
    op.add_column('users', sa.Column('badge', sa.String(), nullable=False, server_default='bronze'))
    op.add_column('users', sa.Column('badge_image_url', sa.String(), nullable=True))


def downgrade():
    # Remove badge columns from users table
    op.drop_column('users', 'badge')
    op.drop_column('users', 'badge_image_url')
