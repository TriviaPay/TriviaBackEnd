"""remove_number_of_entries

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2025-11-01 12:02:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop number_of_entries column from trivia_questions_entries
    op.drop_column('trivia_questions_entries', 'number_of_entries')


def downgrade() -> None:
    # Add back number_of_entries column
    op.add_column('trivia_questions_entries', sa.Column('number_of_entries', sa.Integer(), nullable=True))

