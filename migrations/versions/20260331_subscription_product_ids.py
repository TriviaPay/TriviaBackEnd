"""Add product_id to subscription_plans, populate 4 plans with Stripe/PayPal IDs.

Revision ID: 20260331_sub_product_ids
Revises: 20260328_stripe_non_consumable
"""

from alembic import op
import sqlalchemy as sa

revision = "20260331_sub_product_ids"
down_revision = "20260328_stripe_non_consumable"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add product_id column to subscription_plans
    op.add_column(
        "subscription_plans",
        sa.Column("product_id", sa.String(10), nullable=True),
    )
    op.create_index(
        "ix_subscription_plans_product_id",
        "subscription_plans",
        ["product_id"],
        unique=True,
    )

    # 2. Populate existing plans (id=1: $5/mo, id=2: $10/mo)
    op.execute(
        """
        UPDATE subscription_plans SET
            product_id = 'SUB001',
            stripe_price_id = 'price_1TGXdqEWLqolsVsQpVFCG3Vn',
            paypal_plan_id = 'P-47J10301DR216070UNHF5KHI',
            livemode = true
        WHERE id = 1
        """
    )
    op.execute(
        """
        UPDATE subscription_plans SET
            product_id = 'SUB002',
            stripe_price_id = 'price_1TGXeCEWLqolsVsQPZfK37Bi',
            paypal_plan_id = 'P-9VR38510JS972511ANHF5J4A',
            livemode = true
        WHERE id = 2
        """
    )

    # 3. Insert two new plans ($15/mo, $20/mo)
    op.execute(
        """
        INSERT INTO subscription_plans
            (name, description, price_usd, billing_interval, unit_amount_minor, currency,
             interval, interval_count, product_id, stripe_price_id, paypal_plan_id, livemode,
             created_at, updated_at)
        VALUES
            ('$15 Monthly Subscription', '15MonthlyPro', 15.00, 'month', 1500, 'usd',
             'month', 1, 'SUB003', 'price_1TGXeUEWLqolsVsQvAvap9cc',
             'P-5HS753405P7921541NHF5JQA', true, now(), now())
        """
    )
    op.execute(
        """
        INSERT INTO subscription_plans
            (name, description, price_usd, billing_interval, unit_amount_minor, currency,
             interval, interval_count, product_id, stripe_price_id, paypal_plan_id, livemode,
             created_at, updated_at)
        VALUES
            ('$20 Monthly Subscription', '20MonthlyPro', 20.00, 'month', 2000, 'usd',
             'month', 1, 'SUB004', 'price_1TGXemEWLqolsVsQCpn96Stt',
             'P-89P47043K8309415WNHF5I5Y', true, now(), now())
        """
    )


def downgrade():
    # Remove the two new plans
    op.execute("DELETE FROM subscription_plans WHERE product_id IN ('SUB003', 'SUB004')")

    # Clear product_id and Stripe/PayPal IDs from existing plans
    op.execute(
        """
        UPDATE subscription_plans SET
            product_id = NULL,
            stripe_price_id = NULL,
            paypal_plan_id = NULL
        WHERE id IN (1, 2)
        """
    )

    op.drop_index("ix_subscription_plans_product_id", table_name="subscription_plans")
    op.drop_column("subscription_plans", "product_id")
