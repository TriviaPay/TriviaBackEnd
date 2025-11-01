"""restructure_trivia_questions_daily

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2025-11-01 12:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # First, drop foreign key constraint on account_id if it exists
    # Check if constraint exists before dropping
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    fks = inspector.get_foreign_keys('trivia_questions_daily')
    
    # Find FK constraint on account_id column
    for fk in fks:
        if 'account_id' in fk.get('constrained_columns', []):
            fk_name = fk.get('name')
            if fk_name:
                op.drop_constraint(fk_name, 'trivia_questions_daily', type_='foreignkey')
            break
    
    # Drop user tracking columns (check if they exist first)
    columns = [c['name'] for c in inspector.get_columns('trivia_questions_daily')]
    
    columns_to_drop = ['account_id', 'was_changed', 'user_attempted', 'user_answer', 
                       'user_is_correct', 'user_answered_at', 'answer', 'is_correct', 'answered_at']
    
    for col in columns_to_drop:
        if col in columns:
            op.drop_column('trivia_questions_daily', col)
    
    # Add unique constraints
    op.create_unique_constraint('uq_daily_question_order', 'trivia_questions_daily', ['date', 'question_order'])
    op.create_unique_constraint('uq_daily_question_number', 'trivia_questions_daily', ['date', 'question_number'])


def downgrade() -> None:
    # Remove unique constraints
    op.drop_constraint('uq_daily_question_number', 'trivia_questions_daily', type_='unique')
    op.drop_constraint('uq_daily_question_order', 'trivia_questions_daily', type_='unique')
    
    # Add back columns (with nullable=True for safety)
    op.add_column('trivia_questions_daily', sa.Column('account_id', sa.BigInteger(), nullable=True))
    op.add_column('trivia_questions_daily', sa.Column('was_changed', sa.Boolean(), nullable=True))
    op.add_column('trivia_questions_daily', sa.Column('user_attempted', sa.Boolean(), nullable=True))
    op.add_column('trivia_questions_daily', sa.Column('user_answer', sa.String(), nullable=True))
    op.add_column('trivia_questions_daily', sa.Column('user_is_correct', sa.Boolean(), nullable=True))
    op.add_column('trivia_questions_daily', sa.Column('user_answered_at', sa.DateTime(), nullable=True))
    op.add_column('trivia_questions_daily', sa.Column('answer', sa.String(), nullable=True))
    op.add_column('trivia_questions_daily', sa.Column('is_correct', sa.Boolean(), nullable=True))
    op.add_column('trivia_questions_daily', sa.Column('answered_at', sa.DateTime(), nullable=True))
    
    # Re-add foreign key constraint
    op.create_foreign_key('trivia_questions_daily_account_id_fkey', 'trivia_questions_daily', 'users', ['account_id'], ['account_id'])

