"""remove_price_usd_columns

Revision ID: 7452116ea361
Revises: 351995a3d269
Create Date: 2025-11-22 23:01:16.763064

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7452116ea361'
down_revision: Union[str, None] = '351995a3d269'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop price_usd columns from product tables (safe for production with existence checks)
    connection = op.get_bind()
    
    # Drop price_usd from avatars table
    result = connection.execute(sa.text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name='avatars' AND column_name='price_usd'
    """))
    if result.fetchone() is not None:
        op.drop_column('avatars', 'price_usd')
    
    # Drop price_usd from frames table
    result = connection.execute(sa.text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name='frames' AND column_name='price_usd'
    """))
    if result.fetchone() is not None:
        op.drop_column('frames', 'price_usd')
    
    # Drop price_usd from gem_package_config table
    result = connection.execute(sa.text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name='gem_package_config' AND column_name='price_usd'
    """))
    if result.fetchone() is not None:
        op.drop_column('gem_package_config', 'price_usd')
    
    # Note: badges table doesn't have price_usd, so we skip it


def downgrade() -> None:
    # Recreate price_usd columns (for rollback purposes)
    op.add_column('avatars', sa.Column('price_usd', sa.Float(), nullable=True))
    op.add_column('frames', sa.Column('price_usd', sa.Float(), nullable=True))
    op.add_column('gem_package_config', sa.Column('price_usd', sa.Float(), nullable=False))
    
    # Backfill from price_minor (reverse conversion)
    connection = op.get_bind()
    connection.execute(sa.text("""
        UPDATE avatars 
        SET price_usd = price_minor / 100.0 
        WHERE price_minor IS NOT NULL
    """))
    connection.execute(sa.text("""
        UPDATE frames 
        SET price_usd = price_minor / 100.0 
        WHERE price_minor IS NOT NULL
    """))
    connection.execute(sa.text("""
        UPDATE gem_package_config 
        SET price_usd = price_minor / 100.0 
        WHERE price_minor IS NOT NULL
    """))
