"""admin user and app version tracking

Revision ID: 20260111_support_requests
Revises: 0001_initial_schema
Create Date: 2026-01-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260111_support_requests"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("users", "is_admin")
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("singleton_key", sa.String(), nullable=False, server_default="primary"),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.account_id"]),
        sa.UniqueConstraint("singleton_key", name="uq_admin_users_singleton"),
        sa.UniqueConstraint("user_id", name="uq_admin_users_user_id"),
        sa.UniqueConstraint("email", name="uq_admin_users_email"),
    )
    op.create_index("ix_admin_users_user_id", "admin_users", ["user_id"], unique=True)

    op.create_table(
        "user_device_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("device_uuid", sa.String(), nullable=False),
        sa.Column("device_name", sa.String(), nullable=True),
        sa.Column("app_version", sa.String(), nullable=False),
        sa.Column("os", sa.String(), nullable=False),
        sa.Column("is_latest", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("reported_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.account_id"]),
        sa.UniqueConstraint("user_id", "device_uuid", name="uq_user_device_version"),
    )
    op.create_index("ix_user_device_versions_user_id", "user_device_versions", ["user_id"])
    op.create_index(
        "ix_user_device_versions_device_uuid", "user_device_versions", ["device_uuid"]
    )

    op.create_table(
        "app_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("os", sa.String(), nullable=False),
        sa.Column("latest_version", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("os", name="uq_app_versions_os"),
    )
    op.create_index("ix_app_versions_os", "app_versions", ["os"], unique=True)

    op.add_column(
        "users", sa.Column("device_uuid", sa.String(), nullable=True)
    )


def downgrade():
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=True))
    op.drop_column("users", "device_uuid")
    op.drop_index("ix_app_versions_os", table_name="app_versions")
    op.drop_table("app_versions")

    op.drop_index(
        "ix_user_device_versions_device_uuid", table_name="user_device_versions"
    )
    op.drop_index("ix_user_device_versions_user_id", table_name="user_device_versions")
    op.drop_table("user_device_versions")

    op.drop_index("ix_admin_users_user_id", table_name="admin_users")
    op.drop_table("admin_users")
