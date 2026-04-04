"""Add ad_retry_used column to all trivia attempt tables.

Revision ID: 20260404_ad_retry
Revises: 20260331_sub_product_ids
"""

from alembic import op
import sqlalchemy as sa

revision = "20260404_ad_retry"
down_revision = "20260331_sub_product_ids"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "trivia_user_free_mode_daily",
        sa.Column("ad_retry_used", sa.Boolean, nullable=False, server_default="false"),
    )
    op.add_column(
        "trivia_user_bronze_mode_daily",
        sa.Column("ad_retry_used", sa.Boolean, nullable=False, server_default="false"),
    )
    op.add_column(
        "trivia_user_silver_mode_daily",
        sa.Column("ad_retry_used", sa.Boolean, nullable=False, server_default="false"),
    )


def downgrade():
    op.drop_column("trivia_user_silver_mode_daily", "ad_retry_used")
    op.drop_column("trivia_user_bronze_mode_daily", "ad_retry_used")
    op.drop_column("trivia_user_free_mode_daily", "ad_retry_used")
