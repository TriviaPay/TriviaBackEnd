"""add trivia modes system

Revision ID: add_trivia_modes_system
Revises: add_chat_mute_preferences
Create Date: 2025-01-15 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from datetime import datetime

# revision identifiers, used by Alembic.
revision = 'add_trivia_modes_system'
down_revision = 'add_reply_to_message'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Trivia Mode Configuration table
    op.create_table('trivia_mode_config',
        sa.Column('mode_id', sa.String(), nullable=False),
        sa.Column('mode_name', sa.String(), nullable=False),
        sa.Column('questions_count', sa.Integer(), nullable=False),
        sa.Column('reward_distribution', sa.Text(), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('leaderboard_types', sa.Text(), nullable=False),
        sa.Column('ad_config', sa.Text(), nullable=True),
        sa.Column('survey_config', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('mode_id')
    )
    op.create_index('ix_trivia_mode_config_mode_id', 'trivia_mode_config', ['mode_id'], unique=True)
    
    # Free Mode Questions table
    op.create_table('trivia_questions_free_mode',
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
    op.create_index('ix_trivia_questions_free_mode_id', 'trivia_questions_free_mode', ['id'], unique=False)
    op.create_index('ix_trivia_questions_free_mode_question_hash', 'trivia_questions_free_mode', ['question_hash'], unique=False)
    
    # Free Mode Daily Questions Pool table
    op.create_table('trivia_questions_free_mode_daily',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('date', sa.DateTime(), nullable=False),
        sa.Column('question_id', sa.Integer(), nullable=False),
        sa.Column('question_order', sa.Integer(), nullable=False),
        sa.Column('is_used', sa.Boolean(), nullable=False, server_default='false'),
        sa.ForeignKeyConstraint(['question_id'], ['trivia_questions_free_mode.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('date', 'question_order', name='uq_free_mode_daily_question_order'),
        sa.UniqueConstraint('date', 'question_id', name='uq_free_mode_daily_question_id')
    )
    op.create_index('ix_trivia_questions_free_mode_daily_id', 'trivia_questions_free_mode_daily', ['id'], unique=False)
    
    # User Free Mode Daily Attempts table
    op.create_table('trivia_user_free_mode_daily',
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('question_order', sa.Integer(), nullable=False),
        sa.Column('question_id', sa.Integer(), nullable=False),
        sa.Column('user_answer', sa.String(), nullable=True),
        sa.Column('is_correct', sa.Boolean(), nullable=True),
        sa.Column('answered_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='locked'),
        sa.Column('third_question_completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['users.account_id'], ),
        sa.ForeignKeyConstraint(['question_id'], ['trivia_questions_free_mode.id'], ),
        sa.PrimaryKeyConstraint('account_id', 'date', 'question_order'),
        sa.UniqueConstraint('account_id', 'date', 'question_order', name='uq_user_free_mode_daily_question')
    )
    
    # Free Mode Winners table
    op.create_table('trivia_free_mode_winners',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('draw_date', sa.Date(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('gems_awarded', sa.Integer(), nullable=False),
        sa.Column('double_gems_flag', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('final_gems', sa.Integer(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['account_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_trivia_free_mode_winners_id', 'trivia_free_mode_winners', ['id'], unique=False)
    op.create_index('ix_trivia_free_mode_winners_draw_date', 'trivia_free_mode_winners', ['draw_date'], unique=False)
    
    # Free Mode Leaderboard table
    op.create_table('trivia_free_mode_leaderboard',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('draw_date', sa.Date(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('gems_awarded', sa.Integer(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['account_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_trivia_free_mode_leaderboard_id', 'trivia_free_mode_leaderboard', ['id'], unique=False)
    op.create_index('ix_trivia_free_mode_leaderboard_draw_date', 'trivia_free_mode_leaderboard', ['draw_date'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_trivia_free_mode_leaderboard_draw_date', table_name='trivia_free_mode_leaderboard')
    op.drop_index('ix_trivia_free_mode_leaderboard_id', table_name='trivia_free_mode_leaderboard')
    op.drop_table('trivia_free_mode_leaderboard')
    
    op.drop_index('ix_trivia_free_mode_winners_draw_date', table_name='trivia_free_mode_winners')
    op.drop_index('ix_trivia_free_mode_winners_id', table_name='trivia_free_mode_winners')
    op.drop_table('trivia_free_mode_winners')
    
    op.drop_table('trivia_user_free_mode_daily')
    
    op.drop_index('ix_trivia_questions_free_mode_daily_id', table_name='trivia_questions_free_mode_daily')
    op.drop_table('trivia_questions_free_mode_daily')
    
    op.drop_index('ix_trivia_questions_free_mode_question_hash', table_name='trivia_questions_free_mode')
    op.drop_index('ix_trivia_questions_free_mode_id', table_name='trivia_questions_free_mode')
    op.drop_table('trivia_questions_free_mode')
    
    op.drop_index('ix_trivia_mode_config_mode_id', table_name='trivia_mode_config')
    op.drop_table('trivia_mode_config')

