"""remove_iap_product_map_table

Revision ID: 351995a3d269
Revises: ensure_iap_product_map_001
Create Date: 2025-11-22 22:59:31.194440

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '351995a3d269'
down_revision: Union[str, None] = 'ensure_iap_product_map_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop iap_product_map table if it exists
    connection = op.get_bind()
    result = connection.execute(sa.text("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_name='iap_product_map'
    """))
    
    if result.fetchone() is not None:
        # Drop index first
        op.drop_index('ix_iap_product_map_product_id', table_name='iap_product_map', if_exists=True)
        # Drop table
        op.drop_table('iap_product_map')


def downgrade() -> None:
    # Recreate iap_product_map table (for rollback purposes)
    op.create_table(
        'iap_product_map',
        sa.Column('product_id', sa.String(5), nullable=False),
        sa.Column('credited_amount_minor', sa.BigInteger(), nullable=False),
        sa.Column('platform', sa.String(), nullable=True),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('product_id')
    )
    op.create_index('ix_iap_product_map_product_id', 'iap_product_map', ['product_id'], unique=True)
