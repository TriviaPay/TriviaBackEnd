"""Add Stripe Checkout tables and columns.

Hand-written migration: Alembic only targets models.Base.metadata (sync),
but StripeCheckout and StripeWebhookEvent are defined in app/models/wallet.py
(async Base). All DDL is explicit.

Revision ID: 20260328_stripe_checkout
Revises: 20260327_guest_mode
"""

from alembic import op
import sqlalchemy as sa

revision = "20260328_stripe_checkout"
down_revision = "20260327_guest_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Column additions to existing tables ---

    op.add_column(
        "users",
        sa.Column("stripe_customer_id", sa.String(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_users_stripe_customer_id", "users", ["stripe_customer_id"]
    )

    op.add_column(
        "user_subscriptions",
        sa.Column("stripe_subscription_id", sa.String(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_user_subscriptions_stripe_subscription_id",
        "user_subscriptions",
        ["stripe_subscription_id"],
    )

    op.add_column(
        "subscription_plans",
        sa.Column("stripe_product_id", sa.String(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_subscription_plans_stripe_product_id",
        "subscription_plans",
        ["stripe_product_id"],
    )

    op.add_column(
        "subscription_plans",
        sa.Column("stripe_price_id", sa.String(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_subscription_plans_stripe_price_id",
        "subscription_plans",
        ["stripe_price_id"],
    )

    # --- New tables ---

    op.create_table(
        "stripe_checkouts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.account_id"),
            nullable=False,
        ),
        sa.Column("checkout_session_id", sa.String(), nullable=False),
        sa.Column("payment_intent_id", sa.String(), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(), nullable=True),
        sa.Column("stripe_invoice_id", sa.String(), nullable=True),
        sa.Column("product_id", sa.String(), nullable=False),
        sa.Column("product_type", sa.String(), nullable=False),
        sa.Column("price_minor", sa.BigInteger(), nullable=False),
        sa.Column("gems_credited", sa.Integer(), nullable=True),
        sa.Column(
            "gems_reversed", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "currency", sa.String(), nullable=False, server_default="usd"
        ),
        sa.Column(
            "payment_status",
            sa.String(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "fulfillment_status",
            sa.String(),
            nullable=False,
            server_default="unfulfilled",
        ),
        sa.Column("stripe_customer_id", sa.String(), nullable=True),
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
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        # UNIQUE constraints (these create indexes automatically)
        sa.UniqueConstraint(
            "checkout_session_id", name="uq_stripe_checkouts_session_id"
        ),
        sa.UniqueConstraint(
            "payment_intent_id", name="uq_stripe_checkouts_payment_intent_id"
        ),
    )
    # Non-unique indexes only
    op.create_index(
        "ix_stripe_checkouts_user_id", "stripe_checkouts", ["user_id"]
    )
    op.create_index(
        "ix_stripe_checkouts_stripe_subscription_id",
        "stripe_checkouts",
        ["stripe_subscription_id"],
    )

    op.create_table(
        "stripe_webhook_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default="received",
        ),
        sa.Column("stripe_object_id", sa.String(), nullable=True),
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
        # UNIQUE constraint (creates index automatically)
        sa.UniqueConstraint(
            "event_id", name="uq_stripe_webhook_events_event_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("stripe_webhook_events")
    op.drop_table("stripe_checkouts")

    op.drop_constraint(
        "uq_subscription_plans_stripe_price_id",
        "subscription_plans",
        type_="unique",
    )
    op.drop_column("subscription_plans", "stripe_price_id")
    op.drop_constraint(
        "uq_subscription_plans_stripe_product_id",
        "subscription_plans",
        type_="unique",
    )
    op.drop_column("subscription_plans", "stripe_product_id")

    op.drop_constraint(
        "uq_user_subscriptions_stripe_subscription_id",
        "user_subscriptions",
        type_="unique",
    )
    op.drop_column("user_subscriptions", "stripe_subscription_id")

    op.drop_constraint(
        "uq_users_stripe_customer_id", "users", type_="unique"
    )
    op.drop_column("users", "stripe_customer_id")
