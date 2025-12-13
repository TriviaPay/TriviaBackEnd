"""rename bronze tables and add silver mode

Revision ID: 6808d7879c60
Revises: 9eea773bc177
Create Date: 2025-01-20 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from datetime import datetime

# revision identifiers, used by Alembic.
revision = '6808d7879c60'
down_revision = '9eea773bc177'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename five_dollar_mode tables to bronze_mode
    op.rename_table('trivia_questions_five_dollar_mode', 'trivia_questions_bronze_mode')
    op.rename_table('trivia_questions_five_dollar_mode_daily', 'trivia_questions_bronze_mode_daily')
    op.rename_table('trivia_user_five_dollar_mode_daily', 'trivia_user_bronze_mode_daily')
    op.rename_table('trivia_five_dollar_mode_winners', 'trivia_bronze_mode_winners')
    op.rename_table('trivia_five_dollar_mode_leaderboard', 'trivia_bronze_mode_leaderboard')
    
    # Rename indexes
    op.execute('ALTER INDEX ix_trivia_questions_five_dollar_mode_id RENAME TO ix_trivia_questions_bronze_mode_id')
    op.execute('ALTER INDEX ix_trivia_questions_five_dollar_mode_question_hash RENAME TO ix_trivia_questions_bronze_mode_question_hash')
    op.execute('ALTER INDEX ix_trivia_questions_five_dollar_mode_daily_id RENAME TO ix_trivia_questions_bronze_mode_daily_id')
    op.execute('ALTER INDEX ix_trivia_five_dollar_mode_winners_id RENAME TO ix_trivia_bronze_mode_winners_id')
    op.execute('ALTER INDEX ix_trivia_five_dollar_mode_winners_draw_date RENAME TO ix_trivia_bronze_mode_winners_draw_date')
    op.execute('ALTER INDEX ix_trivia_five_dollar_mode_leaderboard_id RENAME TO ix_trivia_bronze_mode_leaderboard_id')
    op.execute('ALTER INDEX ix_trivia_five_dollar_mode_leaderboard_draw_date RENAME TO ix_trivia_bronze_mode_leaderboard_draw_date')
    
    # Rename foreign key constraints
    op.execute('ALTER TABLE trivia_questions_bronze_mode_daily DROP CONSTRAINT IF EXISTS trivia_questions_five_dollar_mode_daily_question_id_fkey')
    op.create_foreign_key('trivia_questions_bronze_mode_daily_question_id_fkey', 'trivia_questions_bronze_mode_daily', 'trivia_questions_bronze_mode', ['question_id'], ['id'])
    
    op.execute('ALTER TABLE trivia_user_bronze_mode_daily DROP CONSTRAINT IF EXISTS trivia_user_five_dollar_mode_daily_question_id_fkey')
    op.create_foreign_key('trivia_user_bronze_mode_daily_question_id_fkey', 'trivia_user_bronze_mode_daily', 'trivia_questions_bronze_mode', ['question_id'], ['id'])
    
    # Rename unique constraints
    op.execute('ALTER TABLE trivia_questions_bronze_mode_daily DROP CONSTRAINT IF EXISTS uq_five_dollar_mode_daily_question_order')
    op.execute('ALTER TABLE trivia_questions_bronze_mode_daily DROP CONSTRAINT IF EXISTS uq_five_dollar_mode_daily_question_id')
    op.create_unique_constraint('uq_bronze_mode_daily_question_order', 'trivia_questions_bronze_mode_daily', ['date', 'question_order'])
    op.create_unique_constraint('uq_bronze_mode_daily_question_id', 'trivia_questions_bronze_mode_daily', ['date', 'question_id'])
    
    op.execute('ALTER TABLE trivia_user_bronze_mode_daily DROP CONSTRAINT IF EXISTS uq_user_five_dollar_mode_daily')
    op.create_unique_constraint('uq_user_bronze_mode_daily', 'trivia_user_bronze_mode_daily', ['account_id', 'date'])
    
    # Create Silver Mode tables
    # Silver Mode Questions table
    op.create_table('trivia_questions_silver_mode',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('question', sa.String(), nullable=False),
        sa.Column('option_a', sa.String(), nullable=False),
        sa.Column('option_b', sa.String(), nullable=False),
        sa.Column('option_c', sa.String(), nullable=False),
        sa.Column('option_d', sa.String(), nullable=False),
        sa.Column('correct_answer', sa.String(), nullable=False),
        sa.Column('fill_in_answer', sa.String(), nullable=True),
        sa.Column('hint', sa.String(), nullable=True),
        sa.Column('explanation', sa.String(), nullable=True),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('country', sa.String(), nullable=True),
        sa.Column('difficulty_level', sa.String(), nullable=False),
        sa.Column('picture_url', sa.String(), nullable=True),
        sa.Column('question_hash', sa.String(), nullable=False),
        sa.Column('created_date', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('is_used', sa.Boolean(), nullable=False, server_default='false'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_trivia_questions_silver_mode_id', 'trivia_questions_silver_mode', ['id'], unique=False)
    op.create_index('ix_trivia_questions_silver_mode_question_hash', 'trivia_questions_silver_mode', ['question_hash'], unique=False)
    
    # Silver Mode Daily Questions Pool table
    op.create_table('trivia_questions_silver_mode_daily',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('date', sa.DateTime(), nullable=False),
        sa.Column('question_id', sa.Integer(), nullable=False),
        sa.Column('question_order', sa.Integer(), nullable=False),
        sa.Column('is_used', sa.Boolean(), nullable=False, server_default='false'),
        sa.ForeignKeyConstraint(['question_id'], ['trivia_questions_silver_mode.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('date', 'question_order', name='uq_silver_mode_daily_question_order'),
        sa.UniqueConstraint('date', 'question_id', name='uq_silver_mode_daily_question_id')
    )
    op.create_index('ix_trivia_questions_silver_mode_daily_id', 'trivia_questions_silver_mode_daily', ['id'], unique=False)
    
    # User Silver Mode Daily Attempts table
    op.create_table('trivia_user_silver_mode_daily',
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('question_id', sa.Integer(), nullable=False),
        sa.Column('user_answer', sa.String(), nullable=True),
        sa.Column('is_correct', sa.Boolean(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='locked'),
        sa.ForeignKeyConstraint(['account_id'], ['users.account_id'], ),
        sa.ForeignKeyConstraint(['question_id'], ['trivia_questions_silver_mode.id'], ),
        sa.PrimaryKeyConstraint('account_id', 'date'),
        sa.UniqueConstraint('account_id', 'date', name='uq_user_silver_mode_daily')
    )
    
    # Silver Mode Winners table
    op.create_table('trivia_silver_mode_winners',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('draw_date', sa.Date(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('money_awarded', sa.Float(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['account_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_trivia_silver_mode_winners_id', 'trivia_silver_mode_winners', ['id'], unique=False)
    op.create_index('ix_trivia_silver_mode_winners_draw_date', 'trivia_silver_mode_winners', ['draw_date'], unique=False)
    
    # Silver Mode Leaderboard table
    op.create_table('trivia_silver_mode_leaderboard',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('draw_date', sa.Date(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('money_awarded', sa.Float(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['account_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_trivia_silver_mode_leaderboard_id', 'trivia_silver_mode_leaderboard', ['id'], unique=False)
    op.create_index('ix_trivia_silver_mode_leaderboard_draw_date', 'trivia_silver_mode_leaderboard', ['draw_date'], unique=False)


def downgrade() -> None:
    # Drop Silver Mode tables
    op.drop_index('ix_trivia_silver_mode_leaderboard_draw_date', table_name='trivia_silver_mode_leaderboard')
    op.drop_index('ix_trivia_silver_mode_leaderboard_id', table_name='trivia_silver_mode_leaderboard')
    op.drop_table('trivia_silver_mode_leaderboard')
    
    op.drop_index('ix_trivia_silver_mode_winners_draw_date', table_name='trivia_silver_mode_winners')
    op.drop_index('ix_trivia_silver_mode_winners_id', table_name='trivia_silver_mode_winners')
    op.drop_table('trivia_silver_mode_winners')
    
    op.drop_table('trivia_user_silver_mode_daily')
    
    op.drop_index('ix_trivia_questions_silver_mode_daily_id', table_name='trivia_questions_silver_mode_daily')
    op.drop_table('trivia_questions_silver_mode_daily')
    
    op.drop_index('ix_trivia_questions_silver_mode_question_hash', table_name='trivia_questions_silver_mode')
    op.drop_index('ix_trivia_questions_silver_mode_id', table_name='trivia_questions_silver_mode')
    op.drop_table('trivia_questions_silver_mode')
    
    # Rename bronze_mode tables back to five_dollar_mode
    op.rename_table('trivia_questions_bronze_mode', 'trivia_questions_five_dollar_mode')
    op.rename_table('trivia_questions_bronze_mode_daily', 'trivia_questions_five_dollar_mode_daily')
    op.rename_table('trivia_user_bronze_mode_daily', 'trivia_user_five_dollar_mode_daily')
    op.rename_table('trivia_bronze_mode_winners', 'trivia_five_dollar_mode_winners')
    op.rename_table('trivia_bronze_mode_leaderboard', 'trivia_five_dollar_mode_leaderboard')
    
    # Rename indexes back
    op.execute('ALTER INDEX ix_trivia_questions_bronze_mode_id RENAME TO ix_trivia_questions_five_dollar_mode_id')
    op.execute('ALTER INDEX ix_trivia_questions_bronze_mode_question_hash RENAME TO ix_trivia_questions_five_dollar_mode_question_hash')
    op.execute('ALTER INDEX ix_trivia_questions_bronze_mode_daily_id RENAME TO ix_trivia_questions_five_dollar_mode_daily_id')
    op.execute('ALTER INDEX ix_trivia_bronze_mode_winners_id RENAME TO ix_trivia_five_dollar_mode_winners_id')
    op.execute('ALTER INDEX ix_trivia_bronze_mode_winners_draw_date RENAME TO ix_trivia_five_dollar_mode_winners_draw_date')
    op.execute('ALTER INDEX ix_trivia_bronze_mode_leaderboard_id RENAME TO ix_trivia_five_dollar_mode_leaderboard_id')
    op.execute('ALTER INDEX ix_trivia_bronze_mode_leaderboard_draw_date RENAME TO ix_trivia_five_dollar_mode_leaderboard_draw_date')
    
    # Rename foreign key constraints back
    op.execute('ALTER TABLE trivia_questions_five_dollar_mode_daily DROP CONSTRAINT IF EXISTS trivia_questions_bronze_mode_daily_question_id_fkey')
    op.create_foreign_key('trivia_questions_five_dollar_mode_daily_question_id_fkey', 'trivia_questions_five_dollar_mode_daily', 'trivia_questions_five_dollar_mode', ['question_id'], ['id'])
    
    op.execute('ALTER TABLE trivia_user_five_dollar_mode_daily DROP CONSTRAINT IF EXISTS trivia_user_bronze_mode_daily_question_id_fkey')
    op.create_foreign_key('trivia_user_five_dollar_mode_daily_question_id_fkey', 'trivia_user_five_dollar_mode_daily', 'trivia_questions_five_dollar_mode', ['question_id'], ['id'])
    
    # Rename unique constraints back
    op.execute('ALTER TABLE trivia_questions_five_dollar_mode_daily DROP CONSTRAINT IF EXISTS uq_bronze_mode_daily_question_order')
    op.execute('ALTER TABLE trivia_questions_five_dollar_mode_daily DROP CONSTRAINT IF EXISTS uq_bronze_mode_daily_question_id')
    op.create_unique_constraint('uq_five_dollar_mode_daily_question_order', 'trivia_questions_five_dollar_mode_daily', ['date', 'question_order'])
    op.create_unique_constraint('uq_five_dollar_mode_daily_question_id', 'trivia_questions_five_dollar_mode_daily', ['date', 'question_id'])
    
    op.execute('ALTER TABLE trivia_user_five_dollar_mode_daily DROP CONSTRAINT IF EXISTS uq_user_bronze_mode_daily')
    op.create_unique_constraint('uq_user_five_dollar_mode_daily', 'trivia_user_five_dollar_mode_daily', ['account_id', 'date'])
