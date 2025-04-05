"""add username unique constraint

Revision ID: add_username_unique
Revises: 8bcce6c7fa2f
Create Date: 2024-03-31 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_username_unique'
down_revision = '8bcce6c7fa2f'
branch_labels = None
depends_on = None


def upgrade():
    # Add unique constraint to username
    op.create_unique_constraint('uq_users_username', 'users', ['username'])


def downgrade():
    # Remove unique constraint from username
    op.drop_constraint('uq_users_username', 'users', type_='unique') 