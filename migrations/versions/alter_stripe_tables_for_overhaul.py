"""alter_stripe_tables_for_overhaul

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2025-01-27 12:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a8'
down_revision: Union[str, None] = 'a1b2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Alter payment_transactions table
    op.add_column('payment_transactions', sa.Column('amount_minor', sa.BigInteger(), nullable=True))
    op.add_column('payment_transactions', sa.Column('livemode', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('payment_transactions', sa.Column('stripe_customer_id', sa.Text(), nullable=True))
    op.add_column('payment_transactions', sa.Column('charge_id', sa.Text(), nullable=True))
    op.add_column('payment_transactions', sa.Column('refund_id', sa.Text(), nullable=True))
    op.add_column('payment_transactions', sa.Column('balance_transaction_id', sa.Text(), nullable=True))
    op.add_column('payment_transactions', sa.Column('event_id', sa.Text(), nullable=True))
    op.add_column('payment_transactions', sa.Column('idempotency_key', sa.Text(), nullable=True))
    op.add_column('payment_transactions', sa.Column('direction', sa.Text(), nullable=True))  # inbound/outbound/subscription
    op.add_column('payment_transactions', sa.Column('funding_source', sa.Text(), nullable=True))  # card/ach_debit/apple_pay/google_pay/bank_account/internal
    op.add_column('payment_transactions', sa.Column('failure_code', sa.Text(), nullable=True))
    op.add_column('payment_transactions', sa.Column('failure_message', sa.Text(), nullable=True))
    
    # Create indexes for payment_transactions
    op.create_index('pt_user_created_idx', 'payment_transactions', ['user_id', 'created_at'])
    op.create_index('pt_event_idx', 'payment_transactions', ['event_id'])
    op.create_index('pt_payment_intent_unique', 'payment_transactions', ['payment_intent_id'], unique=True, postgresql_where=sa.text('payment_intent_id IS NOT NULL'))
    op.create_index('pt_idem_unique', 'payment_transactions', ['idempotency_key'], unique=True, postgresql_where=sa.text('idempotency_key IS NOT NULL'))
    
    # Add constraint for amount_minor and currency
    op.create_check_constraint(
        'pt_amount_currency_chk',
        'payment_transactions',
        '(amount_minor IS NULL AND currency IS NULL) OR (amount_minor IS NOT NULL AND currency IS NOT NULL)'
    )
    
    # Alter users table
    op.add_column('users', sa.Column('wallet_balance_minor', sa.BigInteger(), nullable=True, server_default='0'))
    op.add_column('users', sa.Column('wallet_currency', sa.Text(), nullable=True, server_default='usd'))
    
    # Alter user_bank_accounts table
    op.add_column('user_bank_accounts', sa.Column('financial_connections_account_id', sa.Text(), nullable=True))
    op.add_column('user_bank_accounts', sa.Column('external_account_id', sa.Text(), nullable=True))
    op.add_column('user_bank_accounts', sa.Column('fingerprint', sa.Text(), nullable=True))
    op.add_column('user_bank_accounts', sa.Column('livemode', sa.Boolean(), nullable=False, server_default='false'))
    
    # Make encrypted fields nullable (deprecate)
    op.alter_column('user_bank_accounts', 'account_number_encrypted', nullable=True)
    op.alter_column('user_bank_accounts', 'routing_number_encrypted', nullable=True)
    
    # Add unique constraint: one default per user
    op.create_index(
        'uba_one_default_per_user',
        'user_bank_accounts',
        ['user_id'],
        unique=True,
        postgresql_where=sa.text('is_default = TRUE')
    )
    
    # Alter subscription_plans table
    op.add_column('subscription_plans', sa.Column('stripe_price_id', sa.Text(), nullable=True))
    op.add_column('subscription_plans', sa.Column('unit_amount_minor', sa.BigInteger(), nullable=True))
    op.add_column('subscription_plans', sa.Column('currency', sa.Text(), nullable=True))
    op.add_column('subscription_plans', sa.Column('interval', sa.Text(), nullable=True))  # day/week/month/year
    op.add_column('subscription_plans', sa.Column('interval_count', sa.Integer(), nullable=True, server_default='1'))
    op.add_column('subscription_plans', sa.Column('trial_period_days', sa.Integer(), nullable=True))
    op.add_column('subscription_plans', sa.Column('tax_behavior', sa.Text(), nullable=True))  # inclusive/exclusive/unspecified
    op.add_column('subscription_plans', sa.Column('livemode', sa.Boolean(), nullable=False, server_default='false'))
    
    # Create unique index on stripe_price_id
    op.create_index('subscription_plans_stripe_price_id_unique', 'subscription_plans', ['stripe_price_id'], unique=True, postgresql_where=sa.text('stripe_price_id IS NOT NULL'))
    
    # Alter user_subscriptions table
    op.add_column('user_subscriptions', sa.Column('stripe_customer_id', sa.Text(), nullable=True))
    op.add_column('user_subscriptions', sa.Column('latest_invoice_id', sa.Text(), nullable=True))
    op.add_column('user_subscriptions', sa.Column('default_payment_method_id', sa.Text(), nullable=True))
    op.add_column('user_subscriptions', sa.Column('pending_setup_intent_id', sa.Text(), nullable=True))
    op.add_column('user_subscriptions', sa.Column('cancel_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('user_subscriptions', sa.Column('canceled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('user_subscriptions', sa.Column('pause_collection', sa.Text(), nullable=True))  # keep_as_draft/mark_uncollectible/void
    op.add_column('user_subscriptions', sa.Column('livemode', sa.Boolean(), nullable=False, server_default='false'))
    
    # Create indexes for user_subscriptions
    op.create_index('us_user_status_idx', 'user_subscriptions', ['user_id', 'status'])
    op.create_index('us_stripe_sub_unique', 'user_subscriptions', ['stripe_subscription_id'], unique=True, postgresql_where=sa.text('stripe_subscription_id IS NOT NULL'))


def downgrade() -> None:
    # Drop indexes
    op.drop_index('us_stripe_sub_unique', table_name='user_subscriptions')
    op.drop_index('us_user_status_idx', table_name='user_subscriptions')
    
    # Drop columns from user_subscriptions
    op.drop_column('user_subscriptions', 'livemode')
    op.drop_column('user_subscriptions', 'pause_collection')
    op.drop_column('user_subscriptions', 'canceled_at')
    op.drop_column('user_subscriptions', 'cancel_at')
    op.drop_column('user_subscriptions', 'pending_setup_intent_id')
    op.drop_column('user_subscriptions', 'default_payment_method_id')
    op.drop_column('user_subscriptions', 'latest_invoice_id')
    op.drop_column('user_subscriptions', 'stripe_customer_id')
    
    # Drop index and columns from subscription_plans
    op.drop_index('subscription_plans_stripe_price_id_unique', table_name='subscription_plans')
    op.drop_column('subscription_plans', 'livemode')
    op.drop_column('subscription_plans', 'tax_behavior')
    op.drop_column('subscription_plans', 'trial_period_days')
    op.drop_column('subscription_plans', 'interval_count')
    op.drop_column('subscription_plans', 'interval')
    op.drop_column('subscription_plans', 'currency')
    op.drop_column('subscription_plans', 'unit_amount_minor')
    op.drop_column('subscription_plans', 'stripe_price_id')
    
    # Drop index and columns from user_bank_accounts
    op.drop_index('uba_one_default_per_user', table_name='user_bank_accounts')
    op.alter_column('user_bank_accounts', 'routing_number_encrypted', nullable=False)
    op.alter_column('user_bank_accounts', 'account_number_encrypted', nullable=False)
    op.drop_column('user_bank_accounts', 'livemode')
    op.drop_column('user_bank_accounts', 'fingerprint')
    op.drop_column('user_bank_accounts', 'external_account_id')
    op.drop_column('user_bank_accounts', 'financial_connections_account_id')
    
    # Drop columns from users
    op.drop_column('users', 'wallet_currency')
    op.drop_column('users', 'wallet_balance_minor')
    
    # Drop constraint and indexes from payment_transactions
    op.drop_constraint('pt_amount_currency_chk', 'payment_transactions', type_='check')
    op.drop_index('pt_idem_unique', table_name='payment_transactions')
    op.drop_index('pt_payment_intent_unique', table_name='payment_transactions')
    op.drop_index('pt_event_idx', table_name='payment_transactions')
    op.drop_index('pt_user_created_idx', table_name='payment_transactions')
    
    # Drop columns from payment_transactions
    op.drop_column('payment_transactions', 'failure_message')
    op.drop_column('payment_transactions', 'failure_code')
    op.drop_column('payment_transactions', 'funding_source')
    op.drop_column('payment_transactions', 'direction')
    op.drop_column('payment_transactions', 'idempotency_key')
    op.drop_column('payment_transactions', 'event_id')
    op.drop_column('payment_transactions', 'balance_transaction_id')
    op.drop_column('payment_transactions', 'refund_id')
    op.drop_column('payment_transactions', 'charge_id')
    op.drop_column('payment_transactions', 'stripe_customer_id')
    op.drop_column('payment_transactions', 'livemode')
    op.drop_column('payment_transactions', 'amount_minor')

