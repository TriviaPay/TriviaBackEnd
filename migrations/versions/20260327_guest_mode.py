"""add guest mode columns to users table

Revision ID: 20260327_guest_mode
Revises: 20260320_payment_indexes
Create Date: 2026-03-27 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260327_guest_mode"
down_revision = "20260320_payment_indexes"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns("users")}

    if "is_guest" not in existing_cols:
        op.add_column(
            "users",
            sa.Column(
                "is_guest",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
        op.create_index("ix_users_is_guest", "users", ["is_guest"])

    if "guest_device_uuid" not in existing_cols:
        op.add_column(
            "users",
            sa.Column("guest_device_uuid", sa.String(), nullable=True),
        )
        op.create_index(
            "ix_users_guest_device_uuid",
            "users",
            ["guest_device_uuid"],
            unique=True,
        )

    if "last_active_at" not in existing_cols:
        op.add_column(
            "users",
            sa.Column("last_active_at", sa.DateTime(), nullable=True),
        )

    if "ad_bonus_claimed" not in existing_cols:
        op.add_column(
            "users",
            sa.Column(
                "ad_bonus_claimed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns("users")}

    if "ad_bonus_claimed" in existing_cols:
        op.drop_column("users", "ad_bonus_claimed")

    if "last_active_at" in existing_cols:
        op.drop_column("users", "last_active_at")

    if "guest_device_uuid" in existing_cols:
        op.drop_index("ix_users_guest_device_uuid", table_name="users")
        op.drop_column("users", "guest_device_uuid")

    if "is_guest" in existing_cols:
        op.drop_index("ix_users_is_guest", table_name="users")
        op.drop_column("users", "is_guest")
