"""remove stripe integration

Revision ID: 20260215_remove_stripe
Revises: 20260111_support_requests
Create Date: 2026-02-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260215_remove_stripe"
down_revision = "20260111_support_requests"
branch_labels = None
depends_on = None


def upgrade():
    # Users: remove Stripe/withdrawal columns
    op.drop_column("users", "stripe_customer_id")
    op.drop_column("users", "stripe_connect_account_id")
    op.drop_column("users", "instant_withdrawal_enabled")
    op.drop_column("users", "instant_withdrawal_daily_limit_minor")

    # Subscription plans: remove Stripe price linkage
    op.drop_column("subscription_plans", "stripe_price_id")

    # User subscriptions: remove Stripe linkage columns
    op.drop_column("user_subscriptions", "stripe_subscription_id")
    op.drop_column("user_subscriptions", "payment_method_id")
    op.drop_column("user_subscriptions", "stripe_customer_id")
    op.drop_column("user_subscriptions", "latest_invoice_id")
    op.drop_column("user_subscriptions", "default_payment_method_id")
    op.drop_column("user_subscriptions", "pending_setup_intent_id")

    # Drop Stripe-specific tables
    op.drop_table("stripe_webhook_events")
    op.drop_table("stripe_reconciliation_snapshots")
    op.drop_table("withdrawal_requests")


def downgrade():
    # Recreate Stripe tables
    op.create_table(
        "stripe_webhook_events",
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("livemode", sa.Boolean(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="received"),
        sa.Column("last_error", sa.String(), nullable=True),
    )

    op.create_table(
        "stripe_reconciliation_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("platform_available_minor", sa.BigInteger(), nullable=False),
        sa.Column("platform_pending_minor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("as_of_date", "currency", name="uq_reconciliation_date_currency"),
    )

    op.create_table(
        "withdrawal_requests",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("fee_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("stripe_payout_id", sa.String(), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("admin_id", sa.BigInteger(), nullable=True),
        sa.Column("admin_notes", sa.Text(), nullable=True),
        sa.Column("livemode", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(["user_id"], ["users.account_id"]),
        sa.ForeignKeyConstraint(["admin_id"], ["users.account_id"]),
    )

    # Restore Stripe columns on users
    op.add_column("users", sa.Column("stripe_customer_id", sa.String(), nullable=True))
    op.add_column("users", sa.Column("stripe_connect_account_id", sa.String(length=255), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "instant_withdrawal_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "instant_withdrawal_daily_limit_minor",
            sa.BigInteger(),
            nullable=False,
            server_default="100000",
        ),
    )
    op.create_index("ix_users_stripe_customer_id", "users", ["stripe_customer_id"])

    # Restore Stripe price link on subscription_plans
    op.add_column("subscription_plans", sa.Column("stripe_price_id", sa.String(), nullable=True))
    op.create_unique_constraint(
        "uq_subscription_plans_stripe_price_id", "subscription_plans", ["stripe_price_id"]
    )

    # Restore Stripe fields on user_subscriptions
    op.add_column("user_subscriptions", sa.Column("stripe_subscription_id", sa.String(), nullable=True))
    op.add_column("user_subscriptions", sa.Column("payment_method_id", sa.String(), nullable=True))
    op.add_column("user_subscriptions", sa.Column("stripe_customer_id", sa.String(), nullable=True))
    op.add_column("user_subscriptions", sa.Column("latest_invoice_id", sa.String(), nullable=True))
    op.add_column("user_subscriptions", sa.Column("default_payment_method_id", sa.String(), nullable=True))
    op.add_column("user_subscriptions", sa.Column("pending_setup_intent_id", sa.String(), nullable=True))
    op.create_unique_constraint(
        "uq_user_subscriptions_stripe_subscription_id",
        "user_subscriptions",
        ["stripe_subscription_id"],
    )
