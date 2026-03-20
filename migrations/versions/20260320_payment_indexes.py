"""add indexes and unique constraint for payment tables

Revision ID: 20260320_payment_indexes
Revises: 20260313_sub_product_ids
Create Date: 2026-03-20 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "20260320_payment_indexes"
down_revision = "20260313_sub_product_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # UserSubscription: prevent duplicate (user_id, plan_id) rows
    op.create_unique_constraint(
        "uq_user_subscriptions_user_plan",
        "user_subscriptions",
        ["user_id", "plan_id"],
    )

    # WalletTransaction: indexes for idempotency and user lookups
    op.create_index(
        "ix_wallet_transactions_event_id",
        "wallet_transactions",
        ["event_id"],
    )
    op.create_index(
        "ix_wallet_transactions_user_id",
        "wallet_transactions",
        ["user_id"],
    )
    op.create_index(
        "ix_wallet_transactions_idempotency_key",
        "wallet_transactions",
        ["idempotency_key"],
    )

    # IapEvent: indexes for webhook dedup lookups
    op.create_index(
        "ix_iap_events_transaction_id",
        "iap_events",
        ["transaction_id"],
    )
    op.create_index(
        "ix_iap_events_purchase_token",
        "iap_events",
        ["purchase_token"],
    )


def downgrade() -> None:
    op.drop_index("ix_iap_events_purchase_token", table_name="iap_events")
    op.drop_index("ix_iap_events_transaction_id", table_name="iap_events")
    op.drop_index("ix_wallet_transactions_idempotency_key", table_name="wallet_transactions")
    op.drop_index("ix_wallet_transactions_user_id", table_name="wallet_transactions")
    op.drop_index("ix_wallet_transactions_event_id", table_name="wallet_transactions")
    op.drop_constraint("uq_user_subscriptions_user_plan", "user_subscriptions", type_="unique")
