"""new_wallet_system

Revision ID: new_wallet_system_001
Revises: add_chat_mute_preferences
Create Date: 2025-01-20 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'new_wallet_system_001'
down_revision: Union[str, None] = 'add_chat_mute_preferences'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Alter users table - check if columns exist first (safe for production)
    connection = op.get_bind()
    
    # Check and add stripe_connect_account_id
    result = connection.execute(sa.text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name='users' AND column_name='stripe_connect_account_id'
    """))
    if result.fetchone() is None:
        op.add_column('users', sa.Column('stripe_connect_account_id', sa.String(255), nullable=True))
    
    # Check and add instant_withdrawal_enabled
    result = connection.execute(sa.text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name='users' AND column_name='instant_withdrawal_enabled'
    """))
    if result.fetchone() is None:
        op.add_column('users', sa.Column('instant_withdrawal_enabled', sa.Boolean(), nullable=False, server_default='true'))
    
    # Check and add instant_withdrawal_daily_limit_minor
    result = connection.execute(sa.text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name='users' AND column_name='instant_withdrawal_daily_limit_minor'
    """))
    if result.fetchone() is None:
        op.add_column('users', sa.Column('instant_withdrawal_daily_limit_minor', sa.BigInteger(), nullable=False, server_default='100000'))
    
    # Create wallet_transactions table (check if exists first)
    result = connection.execute(sa.text("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_name='wallet_transactions'
    """))
    if result.fetchone() is None:
        op.create_table(
            'wallet_transactions',
            sa.Column('id', sa.BigInteger(), nullable=False),
            sa.Column('user_id', sa.BigInteger(), nullable=False),
            sa.Column('amount_minor', sa.BigInteger(), nullable=False),
            sa.Column('currency', sa.String(), nullable=False),
            sa.Column('kind', sa.String(), nullable=False),  # deposit, withdraw, refund, fee, adjustment, etc.
            sa.Column('external_ref_type', sa.String(), nullable=True),  # payment_intent, charge, refund, payout, etc.
            sa.Column('external_ref_id', sa.String(), nullable=True),
            sa.Column('event_id', sa.String(), nullable=True),
            sa.Column('idempotency_key', sa.String(), nullable=True),
            sa.Column('livemode', sa.Boolean(), nullable=False, server_default='false'),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_wallet_transactions_user_id'), 'wallet_transactions', ['user_id'], unique=False)
        op.create_index(op.f('ix_wallet_transactions_created_at'), 'wallet_transactions', ['created_at'], unique=False)
        op.create_index('ix_wallet_transactions_external_ref', 'wallet_transactions', ['external_ref_type', 'external_ref_id', 'kind'], unique=False)
    
    # Create withdrawal_requests table (check if exists first)
    result = connection.execute(sa.text("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_name='withdrawal_requests'
    """))
    if result.fetchone() is None:
        op.create_table(
            'withdrawal_requests',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('amount_minor', sa.BigInteger(), nullable=False),
        sa.Column('currency', sa.String(), nullable=False),
        sa.Column('type', sa.String(), nullable=False),  # standard or instant
        sa.Column('status', sa.String(), nullable=False),  # pending_review, processing, paid, failed, rejected
        sa.Column('fee_minor', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('stripe_payout_id', sa.String(), nullable=True),
        sa.Column('requested_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.Column('admin_id', sa.BigInteger(), nullable=True),
        sa.Column('admin_notes', sa.Text(), nullable=True),
        sa.Column('livemode', sa.Boolean(), nullable=False, server_default='false'),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.ForeignKeyConstraint(['admin_id'], ['users.account_id'], ),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_withdrawal_requests_status_type_account', 'withdrawal_requests', ['status', 'type', 'user_id'], unique=False)
        op.create_index(op.f('ix_withdrawal_requests_user_id'), 'withdrawal_requests', ['user_id'], unique=False)
    
    # Create iap_receipts table (check if exists first)
    result = connection.execute(sa.text("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_name='iap_receipts'
    """))
    if result.fetchone() is None:
        op.create_table(
            'iap_receipts',
            sa.Column('id', sa.BigInteger(), nullable=False),
            sa.Column('user_id', sa.BigInteger(), nullable=False),
            sa.Column('platform', sa.String(), nullable=False),  # apple or google
            sa.Column('transaction_id', sa.String(), nullable=False),
            sa.Column('product_id', sa.String(), nullable=False),
            sa.Column('receipt_data', sa.Text(), nullable=True),
            sa.Column('status', sa.String(), nullable=False),  # verified, failed, consumed
            sa.Column('credited_amount_minor', sa.BigInteger(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_iap_receipts_platform_transaction', 'iap_receipts', ['platform', 'transaction_id'], unique=True)
        op.create_index(op.f('ix_iap_receipts_user_id'), 'iap_receipts', ['user_id'], unique=False)
    
    # Create iap_product_map table (check if exists first)
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
        op.create_index(op.f('ix_iap_product_map_product_id'), 'iap_product_map', ['product_id'], unique=True)
    
    # Add product_id and price_minor to avatars (check if columns exist first)
    for table, col_name in [('avatars', 'product_id'), ('avatars', 'price_minor'),
                           ('frames', 'product_id'), ('frames', 'price_minor'),
                           ('gem_package_config', 'product_id'), ('gem_package_config', 'price_minor'),
                           ('badges', 'product_id'), ('badges', 'price_minor')]:
        result = connection.execute(sa.text(f"""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name='{table}' AND column_name='{col_name}'
        """))
        if result.fetchone() is None:
            if col_name == 'product_id':
                op.add_column(table, sa.Column(col_name, sa.String(5), nullable=True))
            else:
                op.add_column(table, sa.Column(col_name, sa.BigInteger(), nullable=True))
    
    # Create unique constraints (check if they exist first)
    for table, constraint_name in [('avatars', 'uq_avatars_product_id'),
                                  ('frames', 'uq_frames_product_id'),
                                  ('gem_package_config', 'uq_gem_package_config_product_id'),
                                  ('badges', 'uq_badges_product_id')]:
        result = connection.execute(sa.text(f"""
            SELECT constraint_name FROM information_schema.table_constraints 
            WHERE table_name='{table}' AND constraint_name='{constraint_name}'
        """))
        if result.fetchone() is None:
            op.create_unique_constraint(constraint_name, table, ['product_id'])
    
    # Backfill price_minor from price_usd (convert float to cents)
    # Only update rows where price_minor is NULL (safe for re-runs)
    # For avatars
    op.execute("""
        UPDATE avatars 
        SET price_minor = ROUND(COALESCE(price_usd, 0) * 100)::BIGINT
        WHERE price_minor IS NULL AND price_usd IS NOT NULL
    """)
    # Set default for rows without price_usd
    op.execute("""
        UPDATE avatars 
        SET price_minor = 0
        WHERE price_minor IS NULL
    """)
    
    # For frames
    op.execute("""
        UPDATE frames 
        SET price_minor = ROUND(COALESCE(price_usd, 0) * 100)::BIGINT
        WHERE price_minor IS NULL AND price_usd IS NOT NULL
    """)
    op.execute("""
        UPDATE frames 
        SET price_minor = 0
        WHERE price_minor IS NULL
    """)
    
    # For gem_package_config
    op.execute("""
        UPDATE gem_package_config 
        SET price_minor = ROUND(COALESCE(price_usd, 0) * 100)::BIGINT
        WHERE price_minor IS NULL AND price_usd IS NOT NULL
    """)
    op.execute("""
        UPDATE gem_package_config 
        SET price_minor = 0
        WHERE price_minor IS NULL
    """)
    
    # For badges (set to 0 if no price_usd)
    op.execute("""
        UPDATE badges 
        SET price_minor = 0
        WHERE price_minor IS NULL
    """)
    
    # Generate product IDs with prefixes (only for rows without product_id)
    # Use a counter that avoids conflicts by checking existing product_ids
    # Avatars: AV001-AV999
    op.execute("""
        DO $$
        DECLARE
            counter INTEGER := 1;
            new_product_id VARCHAR(5);
            avatar_rec RECORD;
        BEGIN
            FOR avatar_rec IN SELECT id FROM avatars WHERE product_id IS NULL ORDER BY id
            LOOP
                LOOP
                    new_product_id := 'AV' || LPAD(counter::text, 3, '0');
                    -- Check if this product_id already exists
                    IF NOT EXISTS (SELECT 1 FROM avatars WHERE product_id = new_product_id) THEN
                        UPDATE avatars SET product_id = new_product_id WHERE id = avatar_rec.id;
                        EXIT;
                    END IF;
                    counter := counter + 1;
                    -- Safety check to prevent infinite loop
                    IF counter > 999 THEN
                        RAISE EXCEPTION 'Cannot generate unique product_id for avatar %', avatar_rec.id;
                    END IF;
                END LOOP;
                counter := counter + 1;
            END LOOP;
        END $$;
    """)
    
    # Frames: FR001-FR999
    op.execute("""
        DO $$
        DECLARE
            counter INTEGER := 1;
            new_product_id VARCHAR(5);
            frame_rec RECORD;
        BEGIN
            FOR frame_rec IN SELECT id FROM frames WHERE product_id IS NULL ORDER BY id
            LOOP
                LOOP
                    new_product_id := 'FR' || LPAD(counter::text, 3, '0');
                    IF NOT EXISTS (SELECT 1 FROM frames WHERE product_id = new_product_id) THEN
                        UPDATE frames SET product_id = new_product_id WHERE id = frame_rec.id;
                        EXIT;
                    END IF;
                    counter := counter + 1;
                    IF counter > 999 THEN
                        RAISE EXCEPTION 'Cannot generate unique product_id for frame %', frame_rec.id;
                    END IF;
                END LOOP;
                counter := counter + 1;
            END LOOP;
        END $$;
    """)
    
    # Gem packages: GP001-GP999
    op.execute("""
        DO $$
        DECLARE
            counter INTEGER := 1;
            new_product_id VARCHAR(5);
            gem_rec RECORD;
        BEGIN
            FOR gem_rec IN SELECT id FROM gem_package_config WHERE product_id IS NULL ORDER BY id
            LOOP
                LOOP
                    new_product_id := 'GP' || LPAD(counter::text, 3, '0');
                    IF NOT EXISTS (SELECT 1 FROM gem_package_config WHERE product_id = new_product_id) THEN
                        UPDATE gem_package_config SET product_id = new_product_id WHERE id = gem_rec.id;
                        EXIT;
                    END IF;
                    counter := counter + 1;
                    IF counter > 999 THEN
                        RAISE EXCEPTION 'Cannot generate unique product_id for gem package %', gem_rec.id;
                    END IF;
                END LOOP;
                counter := counter + 1;
            END LOOP;
        END $$;
    """)
    
    # Badges: BD001-BD999
    op.execute("""
        DO $$
        DECLARE
            counter INTEGER := 1;
            new_product_id VARCHAR(5);
            badge_rec RECORD;
        BEGIN
            FOR badge_rec IN SELECT id FROM badges WHERE product_id IS NULL ORDER BY id
            LOOP
                LOOP
                    new_product_id := 'BD' || LPAD(counter::text, 3, '0');
                    IF NOT EXISTS (SELECT 1 FROM badges WHERE product_id = new_product_id) THEN
                        UPDATE badges SET product_id = new_product_id WHERE id = badge_rec.id;
                        EXIT;
                    END IF;
                    counter := counter + 1;
                    IF counter > 999 THEN
                        RAISE EXCEPTION 'Cannot generate unique product_id for badge %', badge_rec.id;
                    END IF;
                END LOOP;
                counter := counter + 1;
            END LOOP;
        END $$;
    """)
    
    # Make price_minor NOT NULL after backfill (only if all rows have values)
    # Check that all rows have price_minor set before making it NOT NULL
    connection = op.get_bind()
    
    for table in ['avatars', 'frames', 'gem_package_config', 'badges']:
        result = connection.execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE price_minor IS NULL"))
        null_count = result.scalar()
        if null_count == 0:
            op.alter_column(table, 'price_minor', nullable=False)
        else:
            # If there are NULLs, set them to 0 first
            op.execute(f"UPDATE {table} SET price_minor = 0 WHERE price_minor IS NULL")
            op.alter_column(table, 'price_minor', nullable=False)


def downgrade() -> None:
    # Remove product_id and price_minor columns
    op.drop_constraint('uq_badges_product_id', 'badges', type_='unique')
    op.drop_column('badges', 'price_minor')
    op.drop_column('badges', 'product_id')
    
    op.drop_constraint('uq_gem_package_config_product_id', 'gem_package_config', type_='unique')
    op.drop_column('gem_package_config', 'price_minor')
    op.drop_column('gem_package_config', 'product_id')
    
    op.drop_constraint('uq_frames_product_id', 'frames', type_='unique')
    op.drop_column('frames', 'price_minor')
    op.drop_column('frames', 'product_id')
    
    op.drop_constraint('uq_avatars_product_id', 'avatars', type_='unique')
    op.drop_column('avatars', 'price_minor')
    op.drop_column('avatars', 'product_id')
    
    # Drop tables
    op.drop_index('ix_iap_receipts_user_id', table_name='iap_receipts')
    op.drop_index('ix_iap_receipts_platform_transaction', table_name='iap_receipts')
    op.drop_table('iap_receipts')
    
    op.drop_index(op.f('ix_iap_product_map_product_id'), table_name='iap_product_map')
    op.drop_table('iap_product_map')
    
    op.drop_index('ix_withdrawal_requests_user_id', table_name='withdrawal_requests')
    op.drop_index('ix_withdrawal_requests_status_type_account', table_name='withdrawal_requests')
    op.drop_table('withdrawal_requests')
    
    op.drop_index('ix_wallet_transactions_external_ref', table_name='wallet_transactions')
    op.drop_index(op.f('ix_wallet_transactions_created_at'), table_name='wallet_transactions')
    op.drop_index(op.f('ix_wallet_transactions_user_id'), table_name='wallet_transactions')
    op.drop_table('wallet_transactions')
    
    # Remove columns from users
    op.drop_column('users', 'instant_withdrawal_daily_limit_minor')
    op.drop_column('users', 'instant_withdrawal_enabled')
    op.drop_column('users', 'stripe_connect_account_id')

