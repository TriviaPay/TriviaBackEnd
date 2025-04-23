"""Initialize boost configurations and gem packages

Revision ID: boost_config
Revises: 8ec683b941ba
Create Date: 2023-08-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import table, column
from sqlalchemy import String, Integer, Float, Boolean, DateTime
from datetime import datetime
from typing import Union, Sequence

# revision identifiers, used by Alembic.
revision: str = 'boost_config'
down_revision: Union[str, None] = '8ec683b941ba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # Define tables for bulk insert
    boost_config = table(
        'boost_config',
        column('boost_type', String),
        column('gems_cost', Integer),
        column('description', String),
        column('created_at', DateTime),
        column('updated_at', DateTime)
    )
    
    gem_package_config = table(
        'gem_package_config',
        column('id', Integer),
        column('price_usd', Float),
        column('gems_amount', Integer),
        column('is_one_time', Boolean),
        column('description', String),
        column('created_at', DateTime),
        column('updated_at', DateTime)
    )
    
    # Create new tables
    op.create_table(
        'boost_config',
        sa.Column('boost_type', sa.String(), nullable=False),
        sa.Column('gems_cost', sa.Integer(), nullable=False),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('boost_type')
    )

    op.create_table(
        'gem_package_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('price_usd', sa.Float(), nullable=False),
        sa.Column('gems_amount', sa.Integer(), nullable=False),
        sa.Column('is_one_time', sa.Boolean(), nullable=True),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Insert default boost configurations
    op.bulk_insert(
        boost_config,
        [
            {
                'boost_type': 'streak_saver',
                'gems_cost': 100,
                'description': 'Save your streak',
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            },
            {
                'boost_type': 'question_reroll',
                'gems_cost': 80,
                'description': 'Change your question',
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            },
            {
                'boost_type': 'extra_chance',
                'gems_cost': 150,
                'description': 'Extra chance if you answer wrong',
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            },
            {
                'boost_type': 'hint',
                'gems_cost': 30,
                'description': 'Get a hint for the current question',
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            },
            {
                'boost_type': 'fifty_fifty',
                'gems_cost': 50,
                'description': 'Remove two wrong answers',
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            },
            {
                'boost_type': 'change_question',
                'gems_cost': 10,
                'description': 'Change to a different question',
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            },
            {
                'boost_type': 'auto_submit',
                'gems_cost': 300,
                'description': 'Automatically submit correct answers',
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            }
        ]
    )
    
    # Insert default gem packages
    op.bulk_insert(
        gem_package_config,
        [
            {
                'id': 1,
                'price_usd': 0.99,
                'gems_amount': 500,
                'is_one_time': True,
                'description': 'One-time beginner offer',
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            },
            {
                'id': 2,
                'price_usd': 0.99,
                'gems_amount': 150,
                'is_one_time': False,
                'description': 'Basic gem pack',
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            },
            {
                'id': 3,
                'price_usd': 1.99,
                'gems_amount': 500,
                'is_one_time': False,
                'description': 'Standard gem pack',
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            },
            {
                'id': 4,
                'price_usd': 3.99,
                'gems_amount': 2400,
                'is_one_time': False,
                'description': 'Premium gem pack',
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            },
            {
                'id': 5,
                'price_usd': 5.99,
                'gems_amount': 5000,
                'is_one_time': False,
                'description': 'Super gem pack',
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            },
            {
                'id': 6,
                'price_usd': 9.99,
                'gems_amount': 12000,
                'is_one_time': False,
                'description': 'Ultimate gem pack',
                'created_at': datetime.utcnow(),
                'updated_at': datetime.utcnow()
            }
        ]
    )


def downgrade():
    # Remove tables
    op.drop_table('boost_config')
    op.drop_table('gem_package_config') 