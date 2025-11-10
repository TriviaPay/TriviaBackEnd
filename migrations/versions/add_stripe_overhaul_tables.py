"""add_stripe_overhaul_tables

Revision ID: a1b2c3d4e5f7
Revises: d2a824d58be2
Create Date: 2025-01-27 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, None] = 'd2a824d58be2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create wallet_ledger table
    op.create_table('wallet_ledger',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('currency', sa.Text(), nullable=False),
        sa.Column('delta_minor', sa.BigInteger(), nullable=False),
        sa.Column('balance_after_minor', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),  # deposit/withdraw/refund/fee/adjustment/dispute_hold/dispute_release
        sa.Column('external_ref_type', sa.Text(), nullable=True),  # payment_intent/charge/refund/transfer/payout/balance_transaction/event
        sa.Column('external_ref_id', sa.Text(), nullable=True),
        sa.Column('event_id', sa.Text(), nullable=True),
        sa.Column('idempotency_key', sa.Text(), nullable=True),
        sa.Column('livemode', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('delta_minor <> 0', name='wallet_ledger_amount_chk'),
        sa.CheckConstraint('char_length(currency) BETWEEN 3 AND 10', name='wallet_ledger_currency_chk')
    )
    op.create_index('wallet_ledger_user_created_idx', 'wallet_ledger', ['user_id', 'created_at'])
    op.create_index('wallet_ledger_event_unique', 'wallet_ledger', ['event_id'], unique=True, postgresql_where=sa.text('event_id IS NOT NULL'))
    op.create_index('wallet_ledger_idem_unique', 'wallet_ledger', ['idempotency_key'], unique=True, postgresql_where=sa.text('idempotency_key IS NOT NULL'))
    
    # Create stripe_webhook_events table
    op.create_table('stripe_webhook_events',
        sa.Column('event_id', sa.Text(), nullable=False),
        sa.Column('type', sa.Text(), nullable=False),
        sa.Column('livemode', sa.Boolean(), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.Text(), nullable=False, server_default='received'),  # received/processed/failed
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('event_id')
    )
    op.create_index('stripe_webhook_events_type_idx', 'stripe_webhook_events', ['type'])
    op.create_index('stripe_webhook_events_status_idx', 'stripe_webhook_events', ['status'])
    
    # Create withdrawal_requests table
    op.create_table('withdrawal_requests',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('amount_minor', sa.BigInteger(), nullable=False),
        sa.Column('currency', sa.Text(), nullable=False),
        sa.Column('method', sa.Text(), nullable=False),  # standard/instant
        sa.Column('fee_minor', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('status', sa.Text(), nullable=False),  # pending/approved/processing/paid/failed/canceled
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('admin_id', sa.BigInteger(), nullable=True),
        sa.Column('admin_notes', sa.Text(), nullable=True),
        sa.Column('stripe_transfer_id', sa.Text(), nullable=True),
        sa.Column('stripe_payout_id', sa.Text(), nullable=True),
        sa.Column('stripe_balance_txn_id', sa.Text(), nullable=True),
        sa.Column('event_id', sa.Text(), nullable=True),
        sa.Column('livemode', sa.Boolean(), nullable=False, server_default='false'),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.ForeignKeyConstraint(['admin_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('amount_minor > 0', name='withdrawal_amount_chk'),
        sa.CheckConstraint('fee_minor >= 0', name='withdrawal_fee_chk')
    )
    op.create_index('withdrawal_user_status_idx', 'withdrawal_requests', ['user_id', 'status'])
    op.create_index('withdrawal_status_idx', 'withdrawal_requests', ['status'])
    
    # Create stripe_connected_accounts table
    op.create_table('stripe_connected_accounts',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('account_id', sa.Text(), nullable=False),  # acct_*
        sa.Column('charges_enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('payouts_enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('details_submitted', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('requirements', postgresql.JSONB(), nullable=True),
        sa.Column('livemode', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index('stripe_connected_accounts_account_id_idx', 'stripe_connected_accounts', ['account_id'])
    
    # Create user_wallet_balances table
    op.create_table('user_wallet_balances',
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('currency', sa.Text(), nullable=False),
        sa.Column('balance_minor', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('last_recalculated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('user_id', 'currency')
    )
    op.create_index('user_wallet_balances_user_idx', 'user_wallet_balances', ['user_id'])
    
    # Create stripe_reconciliation_snapshots table
    op.create_table('stripe_reconciliation_snapshots',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('as_of_date', sa.Date(), nullable=False),
        sa.Column('currency', sa.Text(), nullable=False),
        sa.Column('platform_available_minor', sa.BigInteger(), nullable=False),
        sa.Column('platform_pending_minor', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('as_of_date', 'currency')
    )
    op.create_index('stripe_reconciliation_snapshots_date_idx', 'stripe_reconciliation_snapshots', ['as_of_date'])


def downgrade() -> None:
    op.drop_index('stripe_reconciliation_snapshots_date_idx', table_name='stripe_reconciliation_snapshots')
    op.drop_table('stripe_reconciliation_snapshots')
    op.drop_index('user_wallet_balances_user_idx', table_name='user_wallet_balances')
    op.drop_table('user_wallet_balances')
    op.drop_index('stripe_connected_accounts_account_id_idx', table_name='stripe_connected_accounts')
    op.drop_table('stripe_connected_accounts')
    op.drop_index('withdrawal_status_idx', table_name='withdrawal_requests')
    op.drop_index('withdrawal_user_status_idx', table_name='withdrawal_requests')
    op.drop_table('withdrawal_requests')
    op.drop_index('stripe_webhook_events_status_idx', table_name='stripe_webhook_events')
    op.drop_index('stripe_webhook_events_type_idx', table_name='stripe_webhook_events')
    op.drop_table('stripe_webhook_events')
    op.drop_index('wallet_ledger_idem_unique', table_name='wallet_ledger')
    op.drop_index('wallet_ledger_event_unique', table_name='wallet_ledger')
    op.drop_index('wallet_ledger_user_created_idx', table_name='wallet_ledger')
    op.drop_table('wallet_ledger')

