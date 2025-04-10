"""add_question_done_to_trivia

Revision ID: a093e820d8c1
Revises: 628703cb03fc
Create Date: 2024-03-30 21:14:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a093e820d8c1'
down_revision: Union[str, None] = '628703cb03fc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Column already exists, so we skip adding it
    pass


def downgrade() -> None:
    # Since we're not adding the column in upgrade, we don't need to remove it in downgrade
    pass
