"""Create withdrawals table.

Revision ID: 20260404_withdrawals
Revises: 20260404_ad_retry
"""

from alembic import op
import sqlalchemy as sa

revision = "20260404_withdrawals"
down_revision = "20260404_ad_retry"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "withdrawals",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("account_id", sa.BigInteger, sa.ForeignKey("users.account_id"), nullable=False),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("withdrawal_method", sa.String, nullable=False),
        sa.Column("withdrawal_status", sa.String, nullable=False),
        sa.Column("requested_at", sa.DateTime, nullable=False),
        sa.Column("processed_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_withdrawals_account_id", "withdrawals", ["account_id"])


def downgrade():
    op.drop_index("ix_withdrawals_account_id", table_name="withdrawals")
    op.drop_table("withdrawals")
