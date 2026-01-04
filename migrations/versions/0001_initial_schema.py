"""initial_schema

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-01-04
"""

from alembic import context, op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("Initial schema migration requires online mode (DB connection).")

    bind = op.get_bind()
    from models import Base

    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("Initial schema migration requires online mode (DB connection).")

    bind = op.get_bind()
    from models import Base

    Base.metadata.drop_all(bind=bind, checkfirst=True)

