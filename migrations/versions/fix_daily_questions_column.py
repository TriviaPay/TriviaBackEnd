"""Fix daily_questions column name (rename user_id to account_id)

Revision ID: fix_daily_questions_column
Revises: boost_config
Create Date: 2023-08-02 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from typing import Union, Sequence

# revision identifiers, used by Alembic.
revision: str = 'fix_daily_questions_column'
down_revision: Union[str, None] = 'boost_config'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade():
    # Rename the user_id column to account_id in the daily_questions table
    op.alter_column('daily_questions', 'user_id', new_column_name='account_id')

def downgrade():
    # Revert the change
    op.alter_column('daily_questions', 'account_id', new_column_name='user_id') 