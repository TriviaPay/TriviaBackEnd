"""merge store fields and other heads

Revision ID: f2464d91bc85
Revises: 7f1dd52e9cbd, 8bcce6c7fa2f
Create Date: 2025-04-03 20:32:26.391333

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2464d91bc85'
down_revision: Union[str, None] = ('7f1dd52e9cbd', '8bcce6c7fa2f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
