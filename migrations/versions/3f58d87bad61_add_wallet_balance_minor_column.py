"""add_wallet_balance_minor_column

Revision ID: 3f58d87bad61
Revises: 71a88496debd
Create Date: 2025-11-10 00:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f58d87bad61'
down_revision: Union[str, None] = '71a88496debd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add wallet_balance_minor column if it doesn't exist
    op.add_column('users', sa.Column('wallet_balance_minor', sa.BigInteger(), nullable=True, server_default='0'))
    
    # Add wallet_currency column if it doesn't exist
    op.add_column('users', sa.Column('wallet_currency', sa.String(), nullable=True, server_default='usd'))


def downgrade() -> None:
    # Remove columns
    op.drop_column('users', 'wallet_currency')
    op.drop_column('users', 'wallet_balance_minor')
