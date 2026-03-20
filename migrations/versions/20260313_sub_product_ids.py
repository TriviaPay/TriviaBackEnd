"""add apple/google product IDs to subscription_plans

Revision ID: 20260313_sub_product_ids
Revises: 20260215_iap_hardening
Create Date: 2026-03-13 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260313_sub_product_ids"
down_revision = "20260215_iap_hardening"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "subscription_plans" in tables:
        existing_cols = {c["name"] for c in inspector.get_columns("subscription_plans")}

        if "apple_product_id" not in existing_cols:
            op.add_column(
                "subscription_plans",
                sa.Column("apple_product_id", sa.String(), nullable=True),
            )
            op.create_index(
                "ix_subscription_plans_apple_product_id",
                "subscription_plans",
                ["apple_product_id"],
                unique=True,
            )

        if "google_product_id" not in existing_cols:
            op.add_column(
                "subscription_plans",
                sa.Column("google_product_id", sa.String(), nullable=True),
            )
            op.create_index(
                "ix_subscription_plans_google_product_id",
                "subscription_plans",
                ["google_product_id"],
                unique=True,
            )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "subscription_plans" in tables:
        existing_cols = {c["name"] for c in inspector.get_columns("subscription_plans")}

        if "google_product_id" in existing_cols:
            op.drop_index(
                "ix_subscription_plans_google_product_id",
                table_name="subscription_plans",
            )
            op.drop_column("subscription_plans", "google_product_id")

        if "apple_product_id" in existing_cols:
            op.drop_index(
                "ix_subscription_plans_apple_product_id",
                table_name="subscription_plans",
            )
            op.drop_column("subscription_plans", "apple_product_id")
