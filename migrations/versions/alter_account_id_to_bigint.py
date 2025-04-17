"""alter_account_id_to_bigint

Revision ID: alter_account_id_to_bigint
Revises: 18afb0fdcf90
Create Date: 2025-04-16 01:14:28.674915

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'alter_account_id_to_bigint'
down_revision: Union[str, None] = '18afb0fdcf90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Change account_id in user_daily_rewards table from Integer to BigInteger."""
    op.alter_column('user_daily_rewards', 'account_id',
                    existing_type=sa.Integer(),
                    type_=sa.BigInteger(),
                    existing_nullable=False)


def downgrade() -> None:
    """Change account_id in user_daily_rewards table from BigInteger back to Integer."""
    op.alter_column('user_daily_rewards', 'account_id',
                    existing_type=sa.BigInteger(),
                    type_=sa.Integer(),
                    existing_nullable=False) 