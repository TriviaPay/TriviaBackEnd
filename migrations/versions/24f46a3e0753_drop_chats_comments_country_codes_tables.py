"""drop_chats_comments_country_codes_tables

Revision ID: 24f46a3e0753
Revises: 67cb3e65b077
Create Date: 2025-12-15 01:35:06.041744

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '24f46a3e0753'
down_revision: Union[str, None] = '67cb3e65b077'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop tables in order (no dependencies between them, but comments references updates which may not exist)
    op.drop_table('comments')
    op.drop_table('chats')
    op.drop_table('country_codes')


def downgrade() -> None:
    # Recreate tables (simplified structure for rollback)
    op.create_table('country_codes',
        sa.Column('code', sa.String(), nullable=False),
        sa.Column('country_iso', sa.String(), nullable=False),
        sa.Column('country_name', sa.String(), nullable=False),
        sa.Column('flag_url', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('code', 'country_iso')
    )
    
    op.create_table('chats',
        sa.Column('message_id', sa.Integer(), nullable=False),
        sa.Column('sender_account_id', sa.BigInteger(), nullable=False),
        sa.Column('receiver_account_id', sa.BigInteger(), nullable=False),
        sa.Column('message', sa.String(), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=False),
        sa.Column('request_type', sa.String(), nullable=True),
        sa.Column('request_status', sa.String(), nullable=True),
        sa.Column('request_responded_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['receiver_account_id'], ['users.account_id'], ),
        sa.ForeignKeyConstraint(['sender_account_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('message_id')
    )
    
    op.create_table('comments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),
        sa.Column('comment', sa.String(), nullable=False),
        sa.Column('date', sa.DateTime(), nullable=False),
        sa.Column('likes', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
