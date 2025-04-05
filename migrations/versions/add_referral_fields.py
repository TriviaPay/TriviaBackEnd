"""add referral fields

Revision ID: add_referral_fields
Revises: add_username_unique
Create Date: 2024-03-31 11:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


# revision identifiers, used by Alembic.
revision = 'add_referral_fields'
down_revision = 'add_username_unique'
branch_labels = None
depends_on = None


def upgrade():
    # Add new columns
    op.add_column('users', sa.Column('date_of_birth', sa.DateTime(), nullable=True))
    op.add_column('users', sa.Column('referral_code', sa.String(5), nullable=True))
    op.add_column('users', sa.Column('referred_by', sa.String(5), nullable=True))
    op.add_column('users', sa.Column('referral_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('is_referred', sa.Boolean(), nullable=False, server_default='false'))
    
    # Add unique constraint to referral_code
    op.create_unique_constraint('uq_users_referral_code', 'users', ['referral_code'])


def downgrade():
    # Remove columns in reverse order
    op.drop_constraint('uq_users_referral_code', 'users', type_='unique')
    op.drop_column('users', 'is_referred')
    op.drop_column('users', 'referral_count')
    op.drop_column('users', 'referred_by')
    op.drop_column('users', 'referral_code')
    op.drop_column('users', 'date_of_birth') 