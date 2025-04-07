"""Add badge table and update user model

Revision ID: 171597d8d13f
Revises: 1f2dc1c27892
Create Date: 2025-04-06 17:03:45.177714

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '171597d8d13f'
down_revision: Union[str, None] = '1f2dc1c27892'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('badges',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('description', sa.String(), nullable=True),
    sa.Column('image_url', sa.String(), nullable=False),
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_badges_id'), 'badges', ['id'], unique=False)
    op.add_column('users', sa.Column('badge_id', sa.String(), nullable=True))
    op.create_foreign_key(None, 'users', 'badges', ['badge_id'], ['id'])
    op.drop_column('users', 'badge')
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('users', sa.Column('badge', sa.VARCHAR(), server_default=sa.text("'bronze'::character varying"), autoincrement=False, nullable=True))
    op.drop_constraint(None, 'users', type_='foreignkey')
    op.drop_column('users', 'badge_id')
    op.drop_index(op.f('ix_badges_id'), table_name='badges')
    op.drop_table('badges')
    # ### end Alembic commands ###
