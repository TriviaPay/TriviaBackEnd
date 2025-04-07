"""add cosmetics tables

Revision ID: de6f0283a1c2
Revises: a35a065f7638
Create Date: 2023-05-09 12:34:56.789012

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'de6f0283a1c2'
down_revision = 'a35a065f7638'  # This is the actual previous revision ID
branch_labels = None
depends_on = None


def upgrade():
    # Create avatars table
    op.create_table('avatars',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('image_url', sa.String(), nullable=False),
        sa.Column('price_gems', sa.Integer(), nullable=True),
        sa.Column('price_usd', sa.Float(), nullable=True),
        sa.Column('is_premium', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_avatars_id'), 'avatars', ['id'], unique=False)
    
    # Create frames table
    op.create_table('frames',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('image_url', sa.String(), nullable=False),
        sa.Column('price_gems', sa.Integer(), nullable=True),
        sa.Column('price_usd', sa.Float(), nullable=True),
        sa.Column('is_premium', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_frames_id'), 'frames', ['id'], unique=False)
    
    # Create user_avatars table
    op.create_table('user_avatars',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('avatar_id', sa.String(), nullable=False),
        sa.Column('purchase_date', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['avatar_id'], ['avatars.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_avatars_id'), 'user_avatars', ['id'], unique=False)
    
    # Create user_frames table
    op.create_table('user_frames',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('frame_id', sa.String(), nullable=False),
        sa.Column('purchase_date', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['frame_id'], ['frames.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.account_id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_frames_id'), 'user_frames', ['id'], unique=False)
    
    # Add selected_avatar_id and selected_frame_id columns to users table
    op.add_column('users', sa.Column('selected_avatar_id', sa.String(), nullable=True))
    op.add_column('users', sa.Column('selected_frame_id', sa.String(), nullable=True))
    
    # Add foreign key constraints for the new columns
    op.create_foreign_key('fk_users_selected_avatar', 'users', 'avatars', ['selected_avatar_id'], ['id'])
    op.create_foreign_key('fk_users_selected_frame', 'users', 'frames', ['selected_frame_id'], ['id'])


def downgrade():
    # Remove foreign key constraints
    op.drop_constraint('fk_users_selected_avatar', 'users', type_='foreignkey')
    op.drop_constraint('fk_users_selected_frame', 'users', type_='foreignkey')
    
    # Remove columns from users table
    op.drop_column('users', 'selected_avatar_id')
    op.drop_column('users', 'selected_frame_id')
    
    # Drop user_frames table
    op.drop_index(op.f('ix_user_frames_id'), table_name='user_frames')
    op.drop_table('user_frames')
    
    # Drop user_avatars table
    op.drop_index(op.f('ix_user_avatars_id'), table_name='user_avatars')
    op.drop_table('user_avatars')
    
    # Drop frames table
    op.drop_index(op.f('ix_frames_id'), table_name='frames')
    op.drop_table('frames')
    
    # Drop avatars table
    op.drop_index(op.f('ix_avatars_id'), table_name='avatars')
    op.drop_table('avatars') 