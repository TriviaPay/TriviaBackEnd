"""ensure_iap_product_map

Revision ID: ensure_iap_product_map_001
Revises: new_wallet_system_001
Create Date: 2025-01-20 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'ensure_iap_product_map_001'
down_revision: Union[str, None] = 'new_wallet_system_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ensure iap_product_map table exists (safe for re-runs)
    connection = op.get_bind()
    
    result = connection.execute(sa.text("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_name='iap_product_map'
    """))
    
    if result.fetchone() is None:
        op.create_table(
            'iap_product_map',
            sa.Column('product_id', sa.String(5), nullable=False),
            sa.Column('credited_amount_minor', sa.BigInteger(), nullable=False),
            sa.Column('platform', sa.String(), nullable=True),  # 'apple', 'google', or NULL for both
            sa.Column('description', sa.String(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.PrimaryKeyConstraint('product_id')
        )
        op.create_index('ix_iap_product_map_product_id', 'iap_product_map', ['product_id'], unique=True)


def downgrade() -> None:
    # Only drop if exists
    connection = op.get_bind()
    result = connection.execute(sa.text("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_name='iap_product_map'
    """))
    
    if result.fetchone() is not None:
        op.drop_index('ix_iap_product_map_product_id', table_name='iap_product_map')
        op.drop_table('iap_product_map')

