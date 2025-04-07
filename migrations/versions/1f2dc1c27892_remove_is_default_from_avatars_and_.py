"""Remove is_default from avatars and frames, make badge nullable

Revision ID: 1f2dc1c27892
Revises: de6f0283a1c2
Create Date: 2025-04-06 16:57:33.655287

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1f2dc1c27892'
down_revision: Union[str, None] = 'de6f0283a1c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('avatars', 'is_premium',
               existing_type=sa.BOOLEAN(),
               nullable=True,
               existing_server_default=sa.text('false'))
    op.drop_column('avatars', 'is_default')
    op.alter_column('frames', 'is_premium',
               existing_type=sa.BOOLEAN(),
               nullable=True,
               existing_server_default=sa.text('false'))
    op.drop_column('frames', 'is_default')
    op.alter_column('users', 'referral_count',
               existing_type=sa.INTEGER(),
               nullable=True,
               existing_server_default=sa.text('0'))
    op.alter_column('users', 'is_referred',
               existing_type=sa.BOOLEAN(),
               nullable=True,
               existing_server_default=sa.text('false'))
    op.alter_column('users', 'badge',
               existing_type=sa.VARCHAR(),
               nullable=True,
               existing_server_default=sa.text("'bronze'::character varying"))
    op.alter_column('users', 'wallet_balance',
               existing_type=sa.DOUBLE_PRECISION(precision=53),
               nullable=True,
               existing_server_default=sa.text("'0'::double precision"))
    op.alter_column('users', 'total_spent',
               existing_type=sa.DOUBLE_PRECISION(precision=53),
               nullable=True,
               existing_server_default=sa.text("'0'::double precision"))
    op.drop_constraint('fk_users_selected_frame', 'users', type_='foreignkey')
    op.drop_constraint('fk_users_selected_avatar', 'users', type_='foreignkey')
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_foreign_key('fk_users_selected_avatar', 'users', 'avatars', ['selected_avatar_id'], ['id'])
    op.create_foreign_key('fk_users_selected_frame', 'users', 'frames', ['selected_frame_id'], ['id'])
    op.alter_column('users', 'total_spent',
               existing_type=sa.DOUBLE_PRECISION(precision=53),
               nullable=False,
               existing_server_default=sa.text("'0'::double precision"))
    op.alter_column('users', 'wallet_balance',
               existing_type=sa.DOUBLE_PRECISION(precision=53),
               nullable=False,
               existing_server_default=sa.text("'0'::double precision"))
    op.alter_column('users', 'badge',
               existing_type=sa.VARCHAR(),
               nullable=False,
               existing_server_default=sa.text("'bronze'::character varying"))
    op.alter_column('users', 'is_referred',
               existing_type=sa.BOOLEAN(),
               nullable=False,
               existing_server_default=sa.text('false'))
    op.alter_column('users', 'referral_count',
               existing_type=sa.INTEGER(),
               nullable=False,
               existing_server_default=sa.text('0'))
    op.add_column('frames', sa.Column('is_default', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False))
    op.alter_column('frames', 'is_premium',
               existing_type=sa.BOOLEAN(),
               nullable=False,
               existing_server_default=sa.text('false'))
    op.add_column('avatars', sa.Column('is_default', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False))
    op.alter_column('avatars', 'is_premium',
               existing_type=sa.BOOLEAN(),
               nullable=False,
               existing_server_default=sa.text('false'))
    # ### end Alembic commands ###
