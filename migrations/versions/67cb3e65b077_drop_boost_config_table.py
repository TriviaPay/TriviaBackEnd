"""drop_boost_config_table

Revision ID: 67cb3e65b077
Revises: 41040aad79d3
Create Date: 2025-12-15 01:13:52.546505

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '67cb3e65b077'
down_revision: Union[str, None] = '41040aad79d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop boost_config table
    op.drop_table('boost_config')


def downgrade() -> None:
    # Note: This downgrade would require recreating the table with its full schema
    # For now, we'll just document that this is a destructive migration
    # In production, you would need to restore from backup if you need to rollback
    raise NotImplementedError("This migration is destructive and cannot be automatically rolled back. Restore from backup if needed.")
