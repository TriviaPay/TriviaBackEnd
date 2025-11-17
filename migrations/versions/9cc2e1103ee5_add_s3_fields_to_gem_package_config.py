"""add_s3_fields_to_gem_package_config

Revision ID: 9cc2e1103ee5
Revises: b52ef7b16f45
Create Date: 2025-11-16 23:33:29.873896

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9cc2e1103ee5'
down_revision: Union[str, None] = 'b52ef7b16f45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add S3 fields to gem_package_config table
    op.add_column('gem_package_config', sa.Column('bucket', sa.String(), nullable=True))
    op.add_column('gem_package_config', sa.Column('object_key', sa.String(), nullable=True))
    op.add_column('gem_package_config', sa.Column('mime_type', sa.String(), nullable=True))


def downgrade() -> None:
    # Remove S3 fields from gem_package_config table
    op.drop_column('gem_package_config', 'mime_type')
    op.drop_column('gem_package_config', 'object_key')
    op.drop_column('gem_package_config', 'bucket')
