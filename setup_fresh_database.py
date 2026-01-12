"""
Script to set up a fresh database by creating all tables from models,
then stamping the database with the latest Alembic revision.

This is the recommended approach for fresh databases instead of running
migrations that assume existing tables.

Usage:
    python setup_fresh_database.py
"""

import os
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
from dotenv import load_dotenv
from sqlalchemy import inspect

from alembic import command
from core.db import Base, engine

# Load environment variables
load_dotenv()


def setup_fresh_database():
    """Create all tables from models and stamp database with latest revision"""

    print("🔧 Setting up fresh database...")

    # Check if database already has tables
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

    if existing_tables:
        print(f"⚠️  Database already has {len(existing_tables)} tables:")
        for table in existing_tables[:10]:  # Show first 10
            print(f"   - {table}")
        if len(existing_tables) > 10:
            print(f"   ... and {len(existing_tables) - 10} more")

        response = input(
            "\n❓ Continue anyway? This will create missing tables (y/N): "
        )
        if response.lower() != "y":
            print("❌ Aborted.")
            return False

    # Create all tables from models
    print("\n📦 Creating all tables from models...")
    try:
        Base.metadata.create_all(bind=engine)
        print("✅ All tables created successfully!")
    except Exception as e:
        print(f"❌ Error creating tables: {e}")
        return False

    # Get the latest Alembic revision
    print("\n📝 Stamping database with latest Alembic revision...")
    try:
        alembic_cfg = Config("alembic.ini")
        script = ScriptDirectory.from_config(alembic_cfg)
        head_revision = script.get_current_head()

        if head_revision:
            command.stamp(alembic_cfg, head_revision)
            print(f"✅ Database stamped with revision: {head_revision}")
        else:
            print("⚠️  No head revision found in Alembic")
    except Exception as e:
        print(f"❌ Error stamping database: {e}")
        return False

    print("\n✅ Database setup complete!")
    return True


if __name__ == "__main__":
    success = setup_fresh_database()
    sys.exit(0 if success else 1)
