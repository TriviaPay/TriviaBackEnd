"""add_question_done_to_trivia

Revision ID: a093e820d8c1
Revises: 628703cb03fc
Create Date: 2025-03-30 21:14:00.000000

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
    """Add question_done column to trivia table."""
    # Add question_done column with default value False
    op.add_column('trivia', sa.Column('question_done', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    """Remove question_done column from trivia table."""
    op.drop_column('trivia', 'question_done')
