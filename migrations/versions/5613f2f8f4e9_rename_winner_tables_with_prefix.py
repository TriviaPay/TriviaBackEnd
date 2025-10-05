"""rename_winner_tables_with_prefix

Revision ID: 5613f2f8f4e9
Revises: 87739b4dc709
Create Date: 2025-10-04 14:25:13.541881

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5613f2f8f4e9'
down_revision: Union[str, None] = '87739b4dc709'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### Rename tables to use winners_ prefix ###
    # Rename trivia_questions_winners to winners_draw_results
    op.rename_table('trivia_questions_winners', 'winners_draw_results')
    
    # Rename trivia_draw_config to winners_draw_config
    op.rename_table('trivia_draw_config', 'winners_draw_config')
    
    # Rename draw_config to winners_draw_settings
    op.rename_table('draw_config', 'winners_draw_settings')
    
    # Rename indexes to match new table names
    op.execute('ALTER INDEX ix_trivia_questions_winners_id RENAME TO ix_winners_draw_results_id')
    op.execute('ALTER INDEX ix_trivia_draw_config_id RENAME TO ix_winners_draw_config_id')
    op.execute('ALTER INDEX ix_draw_config_id RENAME TO ix_winners_draw_settings_id')


def downgrade() -> None:
    # ### Rename tables back to original names ###
    # Rename indexes back to original names
    op.execute('ALTER INDEX ix_winners_draw_results_id RENAME TO ix_trivia_questions_winners_id')
    op.execute('ALTER INDEX ix_winners_draw_config_id RENAME TO ix_trivia_draw_config_id')
    op.execute('ALTER INDEX ix_winners_draw_settings_id RENAME TO ix_draw_config_id')
    
    # Rename tables back to original names
    op.rename_table('winners_draw_results', 'trivia_questions_winners')
    op.rename_table('winners_draw_config', 'trivia_draw_config')
    op.rename_table('winners_draw_settings', 'draw_config')
