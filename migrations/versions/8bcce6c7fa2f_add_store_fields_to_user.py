"""add store fields to user

Revision ID: 8bcce6c7fa2f
Revises: badge_columns
Create Date: 2024-03-30 21:14:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8bcce6c7fa2f'
down_revision = 'badge_columns'
branch_labels = None
depends_on = None


def upgrade():
    # Add store-related fields
    op.add_column('users', sa.Column('owned_cosmetics', sa.String(), nullable=True))
    op.add_column('users', sa.Column('owned_boosts', sa.String(), nullable=True))


def downgrade():
    # Remove store-related fields
    op.drop_column('users', 'owned_cosmetics')
    op.drop_column('users', 'owned_boosts')
