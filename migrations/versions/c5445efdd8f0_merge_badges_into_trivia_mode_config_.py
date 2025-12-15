"""merge_badges_into_trivia_mode_config_and_drop_legacy_tables

Revision ID: c5445efdd8f0
Revises: 24f46a3e0753
Create Date: 2025-12-15 01:48:54.795146

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5445efdd8f0'
down_revision: Union[str, None] = '24f46a3e0753'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    
    # Check if columns already exist (in case of partial migration)
    result = connection.execute(sa.text("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'trivia_mode_config' 
        AND column_name = 'badge_image_url'
    """))
    columns_exist = result.fetchone() is not None
    
    if not columns_exist:
        # Add badge columns to trivia_mode_config
        op.add_column('trivia_mode_config', sa.Column('badge_image_url', sa.String(), nullable=True))
        op.add_column('trivia_mode_config', sa.Column('badge_description', sa.String(), nullable=True))
        op.add_column('trivia_mode_config', sa.Column('badge_level', sa.Integer(), nullable=True))
        op.add_column('trivia_mode_config', sa.Column('badge_product_id', sa.String(length=5), nullable=True))
        op.add_column('trivia_mode_config', sa.Column('badge_price_minor', sa.BigInteger(), nullable=True))
    
    # Create unique index on badge_product_id if it doesn't exist
    result = connection.execute(sa.text("""
        SELECT indexname 
        FROM pg_indexes 
        WHERE tablename = 'trivia_mode_config' 
        AND indexname = 'ix_trivia_mode_config_badge_product_id'
    """))
    index_exists = result.fetchone() is not None
    
    if not index_exists:
        op.create_index('ix_trivia_mode_config_badge_product_id', 'trivia_mode_config', ['badge_product_id'], unique=True, postgresql_where=sa.text('badge_product_id IS NOT NULL'))
    
    # Update foreign key constraint for users.badge_id to reference trivia_mode_config.mode_id
    # First check and drop the old foreign key if it exists
    result = connection.execute(sa.text("""
        SELECT constraint_name 
        FROM information_schema.table_constraints 
        WHERE table_name = 'users' 
        AND constraint_name = 'users_badge_id_fkey'
    """))
    constraint_exists = result.fetchone() is not None
    
    if constraint_exists:
        op.drop_constraint('users_badge_id_fkey', 'users', type_='foreignkey')
    
    # CRITICAL: Clean up invalid badge_id references before creating new foreign key
    # Set badge_id to NULL for users where badge_id doesn't exist in trivia_mode_config
    connection = op.get_bind()
    connection.execute(sa.text("""
        UPDATE users
        SET badge_id = NULL
        WHERE badge_id IS NOT NULL
        AND badge_id NOT IN (SELECT mode_id FROM trivia_mode_config)
    """))
    
    # Migrate badge data to trivia_mode_config (if badges table exists)
    # Note: This assumes badge.id matches mode_id in trivia_mode_config
    try:
        # Check if badges table exists and has data
        result = connection.execute(sa.text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'badges'
            )
        """))
        badges_table_exists = result.scalar()
        
        if badges_table_exists:
            # Migrate badge data to trivia_mode_config
            # This assumes badge.id can be matched to mode_id
            connection.execute(sa.text("""
                UPDATE trivia_mode_config tmc
                SET 
                    badge_image_url = b.image_url,
                    badge_description = b.description,
                    badge_level = b.level,
                    badge_product_id = b.product_id,
                    badge_price_minor = b.price_minor
                FROM badges b
                WHERE tmc.mode_id = b.id
            """))
    except Exception as e:
        # If migration fails, log but continue (badges table may already be dropped)
        print(f"Note: Could not migrate badge data: {e}")
    
    # Add new foreign key constraint (now that all invalid references are cleaned up)
    op.create_foreign_key(
        'users_badge_id_fkey',
        'users', 'trivia_mode_config',
        ['badge_id'], ['mode_id']
    )
    
    # Drop legacy tables
    # Drop in order to handle foreign key dependencies
    # First drop tables with foreign keys that reference other tables
    op.drop_table('trivia_user_daily')  # References trivia.question_number
    op.drop_table('trivia_questions_daily')  # References trivia.question_number
    op.drop_table('trivia_questions_entries')  # References users.account_id
    op.drop_table('trivia')  # No dependencies
    op.drop_table('badges')
    op.drop_table('letters')
    op.drop_table('updates')
    op.drop_table('winners_draw_results')  # Drop before winners_draw_config if there's a dependency
    op.drop_table('winners_draw_config')
    op.drop_table('withdrawals')


def downgrade() -> None:
    # Recreate dropped tables (simplified structure for rollback)
    
    # Recreate withdrawals table
    op.create_table('withdrawals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('withdrawal_method', sa.String(), nullable=False),
        sa.Column('withdrawal_status', sa.String(), nullable=False),
        sa.Column('requested_at', sa.DateTime(), nullable=False),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Recreate trivia table (simplified structure for rollback)
    op.create_table('trivia',
        sa.Column('question_number', sa.Integer(), nullable=False),
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
        sa.Column('created_date', sa.DateTime(), nullable=False),
        sa.Column('question_done', sa.Boolean(), nullable=True),
        sa.Column('que_displayed_date', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('question_number')
    )
    
    # Recreate trivia_questions_daily table
    op.create_table('trivia_questions_daily',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('date', sa.DateTime(), nullable=False),
        sa.Column('question_number', sa.Integer(), nullable=False),
        sa.Column('question_order', sa.Integer(), nullable=False),
        sa.Column('is_common', sa.Boolean(), nullable=True),
        sa.Column('is_used', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['question_number'], ['trivia.question_number'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('date', 'question_order', name='uq_daily_question_order'),
        sa.UniqueConstraint('date', 'question_number', name='uq_daily_question_number')
    )
    
    # Recreate trivia_user_daily table
    op.create_table('trivia_user_daily',
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('question_order', sa.Integer(), nullable=False),
        sa.Column('question_number', sa.Integer(), nullable=False),
        sa.Column('unlock_method', sa.String(), nullable=True),
        sa.Column('viewed_at', sa.DateTime(), nullable=True),
        sa.Column('user_answer', sa.String(), nullable=True),
        sa.Column('is_correct', sa.Boolean(), nullable=True),
        sa.Column('answered_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('retry_count', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['users.account_id'], ),
        sa.ForeignKeyConstraint(['question_number'], ['trivia.question_number'], ),
        sa.PrimaryKeyConstraint('account_id', 'date', 'question_order'),
        sa.UniqueConstraint('account_id', 'date', 'question_order', name='uq_user_daily_question')
    )
    
    # Recreate trivia_questions_entries table
    op.create_table('trivia_questions_entries',
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('ques_attempted', sa.Integer(), nullable=False),
        sa.Column('correct_answers', sa.Integer(), nullable=False),
        sa.Column('wrong_answers', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('account_id', 'date')
    )
    
    # Recreate winners_draw_config table
    op.create_table('winners_draw_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('is_custom', sa.Boolean(), nullable=True),
        sa.Column('custom_winner_count', sa.Integer(), nullable=True),
        sa.Column('custom_data', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Recreate winners_draw_results table
    op.create_table('winners_draw_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('prize_amount', sa.Float(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('draw_date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Recreate updates table
    op.create_table('updates',
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('picture_url', sa.String(), nullable=True),
        sa.Column('post_date', sa.DateTime(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('likes', sa.Integer(), nullable=True),
        sa.Column('shares', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('post_id')
    )
    
    # Recreate letters table
    op.create_table('letters',
        sa.Column('letter', sa.String(), nullable=False),
        sa.Column('image_url', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('letter')
    )
    
    # Recreate badges table
    op.create_table('badges',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('image_url', sa.String(), nullable=False),
        sa.Column('level', sa.Integer(), nullable=False),
        sa.Column('product_id', sa.String(length=5), nullable=True),
        sa.Column('price_minor', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Restore foreign key constraint for users.badge_id
    op.drop_constraint('users_badge_id_fkey', 'users', type_='foreignkey')
    op.create_foreign_key(
        'users_badge_id_fkey',
        'users', 'badges',
        ['badge_id'], ['id']
    )
    
    # Remove badge columns from trivia_mode_config
    op.drop_index('ix_trivia_mode_config_badge_product_id', table_name='trivia_mode_config')
    op.drop_column('trivia_mode_config', 'badge_price_minor')
    op.drop_column('trivia_mode_config', 'badge_product_id')
    op.drop_column('trivia_mode_config', 'badge_level')
    op.drop_column('trivia_mode_config', 'badge_description')
    op.drop_column('trivia_mode_config', 'badge_image_url')
