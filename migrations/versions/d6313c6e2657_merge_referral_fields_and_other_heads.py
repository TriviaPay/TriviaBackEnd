"""merge referral fields and other heads

Revision ID: d6313c6e2657
Revises: add_referral_fields, f2464d91bc85
Create Date: 2025-04-05 00:56:18.536921

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6313c6e2657'
down_revision: Union[str, None] = ('add_referral_fields', 'f2464d91bc85')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
