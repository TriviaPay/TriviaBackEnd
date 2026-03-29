"""Add asset_granted column to stripe_checkouts for non-consumable support.

Hand-written migration.

Revision ID: 20260328_stripe_non_consumable
Revises: 20260328_paypal_checkout
"""

from alembic import op
import sqlalchemy as sa

revision = "20260328_stripe_non_consumable"
down_revision = "20260328_paypal_checkout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stripe_checkouts",
        sa.Column("asset_granted", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("stripe_checkouts", "asset_granted")
