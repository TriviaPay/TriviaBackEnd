"""add five dollar mode tables

Revision ID: 9eea773bc177
Revises: add_trivia_modes_system
Create Date: 2025-01-15 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from datetime import datetime

# revision identifiers, used by Alembic.
revision = '9eea773bc177'
down_revision = 'add_trivia_modes_system'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # $5 Mode Questions table
    op.create_table('trivia_questions_five_dollar_mode',
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
    op.create_index('ix_trivia_questions_five_dollar_mode_id', 'trivia_questions_five_dollar_mode', ['id'], unique=False)
    op.create_index('ix_trivia_questions_five_dollar_mode_question_hash', 'trivia_questions_five_dollar_mode', ['question_hash'], unique=False)
    
    # $5 Mode Daily Questions Pool table
    op.create_table('trivia_questions_five_dollar_mode_daily',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('date', sa.DateTime(), nullable=False),
        sa.Column('question_id', sa.Integer(), nullable=False),
        sa.Column('question_order', sa.Integer(), nullable=False),
        sa.Column('is_used', sa.Boolean(), nullable=False, server_default='false'),
        sa.ForeignKeyConstraint(['question_id'], ['trivia_questions_five_dollar_mode.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('date', 'question_order', name='uq_five_dollar_mode_daily_question_order'),
        sa.UniqueConstraint('date', 'question_id', name='uq_five_dollar_mode_daily_question_id')
    )
    op.create_index('ix_trivia_questions_five_dollar_mode_daily_id', 'trivia_questions_five_dollar_mode_daily', ['id'], unique=False)
    
    # User $5 Mode Daily Attempts table
    op.create_table('trivia_user_five_dollar_mode_daily',
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('question_id', sa.Integer(), nullable=False),
        sa.Column('user_answer', sa.String(), nullable=True),
        sa.Column('is_correct', sa.Boolean(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='locked'),
        sa.ForeignKeyConstraint(['account_id'], ['users.account_id'], ),
        sa.ForeignKeyConstraint(['question_id'], ['trivia_questions_five_dollar_mode.id'], ),
        sa.PrimaryKeyConstraint('account_id', 'date'),
        sa.UniqueConstraint('account_id', 'date', name='uq_user_five_dollar_mode_daily')
    )
    
    # $5 Mode Winners table
    op.create_table('trivia_five_dollar_mode_winners',
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
    op.create_index('ix_trivia_five_dollar_mode_winners_id', 'trivia_five_dollar_mode_winners', ['id'], unique=False)
    op.create_index('ix_trivia_five_dollar_mode_winners_draw_date', 'trivia_five_dollar_mode_winners', ['draw_date'], unique=False)
    
    # $5 Mode Leaderboard table
    op.create_table('trivia_five_dollar_mode_leaderboard',
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
    op.create_index('ix_trivia_five_dollar_mode_leaderboard_id', 'trivia_five_dollar_mode_leaderboard', ['id'], unique=False)
    op.create_index('ix_trivia_five_dollar_mode_leaderboard_draw_date', 'trivia_five_dollar_mode_leaderboard', ['draw_date'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_trivia_five_dollar_mode_leaderboard_draw_date', table_name='trivia_five_dollar_mode_leaderboard')
    op.drop_index('ix_trivia_five_dollar_mode_leaderboard_id', table_name='trivia_five_dollar_mode_leaderboard')
    op.drop_table('trivia_five_dollar_mode_leaderboard')
    
    op.drop_index('ix_trivia_five_dollar_mode_winners_draw_date', table_name='trivia_five_dollar_mode_winners')
    op.drop_index('ix_trivia_five_dollar_mode_winners_id', table_name='trivia_five_dollar_mode_winners')
    op.drop_table('trivia_five_dollar_mode_winners')
    
    op.drop_table('trivia_user_five_dollar_mode_daily')
    
    op.drop_index('ix_trivia_questions_five_dollar_mode_daily_id', table_name='trivia_questions_five_dollar_mode_daily')
    op.drop_table('trivia_questions_five_dollar_mode_daily')
    
    op.drop_index('ix_trivia_questions_five_dollar_mode_question_hash', table_name='trivia_questions_five_dollar_mode')
    op.drop_index('ix_trivia_questions_five_dollar_mode_id', table_name='trivia_questions_five_dollar_mode')
    op.drop_table('trivia_questions_five_dollar_mode')
