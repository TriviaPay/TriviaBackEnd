"""add_wallet_fields_to_user

Revision ID: 58355ef047f4
Revises: a093e820d8c1
Create Date: 2025-03-31 22:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '58355ef047f4'
down_revision: Union[str, None] = 'a093e820d8c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add wallet-related columns to users table
    op.add_column('users', sa.Column('wallet_balance', sa.Float, nullable=False, server_default='0.0'))
    op.add_column('users', sa.Column('total_spent', sa.Float, nullable=False, server_default='0.0'))
    op.add_column('users', sa.Column('last_wallet_update', sa.DateTime, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove wallet-related columns from users table
    op.drop_column('users', 'wallet_balance')
    op.drop_column('users', 'total_spent')
    op.drop_column('users', 'last_wallet_update')
