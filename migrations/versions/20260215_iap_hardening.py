"""iap hardening and product types

Revision ID: 20260215_iap_hardening
Revises: 20260215_remove_stripe
Create Date: 2026-02-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260215_iap_hardening"
down_revision = "20260215_remove_stripe"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "avatars" in tables:
        op.add_column(
            "avatars",
            sa.Column(
                "product_type",
                sa.String(),
                nullable=False,
                server_default="non_consumable",
            ),
        )
    if "frames" in tables:
        op.add_column(
            "frames",
            sa.Column(
                "product_type",
                sa.String(),
                nullable=False,
                server_default="non_consumable",
            ),
        )
    if "gem_package_config" in tables:
        op.add_column(
            "gem_package_config",
            sa.Column(
                "product_type",
                sa.String(),
                nullable=False,
                server_default="consumable",
            ),
        )
    if "badges" in tables:
        op.add_column(
            "badges",
            sa.Column(
                "product_type",
                sa.String(),
                nullable=False,
                server_default="non_consumable",
            ),
        )

    if "iap_receipts" in tables:
        op.add_column("iap_receipts", sa.Column("original_transaction_id", sa.String()))
        op.add_column("iap_receipts", sa.Column("web_order_line_item_id", sa.String()))
        op.add_column("iap_receipts", sa.Column("bundle_id", sa.String()))
        op.add_column("iap_receipts", sa.Column("environment", sa.String()))
        op.add_column("iap_receipts", sa.Column("product_type", sa.String()))
        op.add_column("iap_receipts", sa.Column("purchase_token", sa.String()))
        op.add_column("iap_receipts", sa.Column("purchase_time_ms", sa.BigInteger()))
        op.add_column("iap_receipts", sa.Column("purchase_state", sa.Integer()))
        op.add_column("iap_receipts", sa.Column("acknowledgement_state", sa.Integer()))
        op.add_column("iap_receipts", sa.Column("revocation_date", sa.DateTime()))
        op.add_column("iap_receipts", sa.Column("revocation_reason", sa.String()))
        op.add_column("iap_receipts", sa.Column("app_account_token", sa.String()))
        op.create_unique_constraint(
            "uq_iap_receipts_platform_purchase_token",
            "iap_receipts",
            ["platform", "purchase_token"],
        )

    if "iap_events" not in tables:
        op.create_table(
            "iap_events",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("platform", sa.String(), nullable=False),
            sa.Column("event_id", sa.String(), nullable=False),
            sa.Column("notification_type", sa.String(), nullable=True),
            sa.Column("subtype", sa.String(), nullable=True),
            sa.Column("transaction_id", sa.String(), nullable=True),
            sa.Column("purchase_token", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="received"),
            sa.Column("raw_payload", sa.Text(), nullable=True),
            sa.Column("received_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("event_id", name="uq_iap_events_event_id"),
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "iap_events" in tables:
        op.drop_table("iap_events")

    if "iap_receipts" in tables:
        op.drop_constraint(
            "uq_iap_receipts_platform_purchase_token",
            "iap_receipts",
            type_="unique",
        )
        op.drop_column("iap_receipts", "app_account_token")
        op.drop_column("iap_receipts", "revocation_reason")
        op.drop_column("iap_receipts", "revocation_date")
        op.drop_column("iap_receipts", "acknowledgement_state")
        op.drop_column("iap_receipts", "purchase_state")
        op.drop_column("iap_receipts", "purchase_time_ms")
        op.drop_column("iap_receipts", "purchase_token")
        op.drop_column("iap_receipts", "product_type")
        op.drop_column("iap_receipts", "environment")
        op.drop_column("iap_receipts", "bundle_id")
        op.drop_column("iap_receipts", "web_order_line_item_id")
        op.drop_column("iap_receipts", "original_transaction_id")

    if "badges" in tables:
        op.drop_column("badges", "product_type")
    if "gem_package_config" in tables:
        op.drop_column("gem_package_config", "product_type")
    if "frames" in tables:
        op.drop_column("frames", "product_type")
    if "avatars" in tables:
        op.drop_column("avatars", "product_type")
