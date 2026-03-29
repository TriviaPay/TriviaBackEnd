"""Add PayPal Checkout tables and columns.

Hand-written migration: PayPalCheckout and PayPalWebhookEvent are defined
in app/models/wallet.py (async Base). All DDL is explicit.

Revision ID: 20260328_paypal_checkout
Revises: 20260328_stripe_checkout
"""

from alembic import op
import sqlalchemy as sa

revision = "20260328_paypal_checkout"
down_revision = "20260328_stripe_checkout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Column additions to existing tables ---

    op.add_column(
        "users",
        sa.Column("paypal_payer_id", sa.String(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_users_paypal_payer_id", "users", ["paypal_payer_id"]
    )

    op.add_column(
        "user_subscriptions",
        sa.Column("paypal_subscription_id", sa.String(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_user_subscriptions_paypal_subscription_id",
        "user_subscriptions",
        ["paypal_subscription_id"],
    )

    op.add_column(
        "subscription_plans",
        sa.Column("paypal_product_id", sa.String(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_subscription_plans_paypal_product_id",
        "subscription_plans",
        ["paypal_product_id"],
    )

    op.add_column(
        "subscription_plans",
        sa.Column("paypal_plan_id", sa.String(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_subscription_plans_paypal_plan_id",
        "subscription_plans",
        ["paypal_plan_id"],
    )

    # --- New tables ---

    op.create_table(
        "paypal_checkouts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.account_id"),
            nullable=False,
        ),
        sa.Column("paypal_order_id", sa.String(), nullable=True),
        sa.Column("paypal_capture_id", sa.String(), nullable=True),
        sa.Column("paypal_subscription_id", sa.String(), nullable=True),
        sa.Column("product_id", sa.String(), nullable=False),
        sa.Column("product_type", sa.String(), nullable=False),
        sa.Column("price_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "currency", sa.String(), nullable=False, server_default="usd"
        ),
        sa.Column("gems_credited", sa.Integer(), nullable=True),
        sa.Column(
            "gems_reversed", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "asset_granted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "payment_status",
            sa.String(),
            nullable=False,
            server_default="created",
        ),
        sa.Column(
            "fulfillment_status",
            sa.String(),
            nullable=False,
            server_default="unfulfilled",
        ),
        sa.Column("paypal_payer_id", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column(
            "livemode",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("captured_at", sa.DateTime(), nullable=True),
        # UNIQUE constraints
        sa.UniqueConstraint(
            "paypal_order_id", name="uq_paypal_checkouts_order_id"
        ),
        sa.UniqueConstraint(
            "paypal_capture_id", name="uq_paypal_checkouts_capture_id"
        ),
        sa.UniqueConstraint(
            "paypal_subscription_id",
            name="uq_paypal_checkouts_subscription_id",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_paypal_checkouts_idempotency_key"
        ),
    )
    op.create_index(
        "ix_paypal_checkouts_user_id", "paypal_checkouts", ["user_id"]
    )

    op.create_table(
        "paypal_webhook_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default="received",
        ),
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column("raw_payload", sa.Text(), nullable=True),
        sa.Column(
            "livemode",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "received_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "event_id", name="uq_paypal_webhook_events_event_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("paypal_webhook_events")
    op.drop_table("paypal_checkouts")

    op.drop_constraint(
        "uq_subscription_plans_paypal_plan_id",
        "subscription_plans",
        type_="unique",
    )
    op.drop_column("subscription_plans", "paypal_plan_id")
    op.drop_constraint(
        "uq_subscription_plans_paypal_product_id",
        "subscription_plans",
        type_="unique",
    )
    op.drop_column("subscription_plans", "paypal_product_id")

    op.drop_constraint(
        "uq_user_subscriptions_paypal_subscription_id",
        "user_subscriptions",
        type_="unique",
    )
    op.drop_column("user_subscriptions", "paypal_subscription_id")

    op.drop_constraint(
        "uq_users_paypal_payer_id", "users", type_="unique"
    )
    op.drop_column("users", "paypal_payer_id")
