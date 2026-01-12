#!/usr/bin/env python3

import os
import sys

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text

from core.db import engine


def add_display_name_column():
    """Add the missing display_name column to the users table"""
    print("Adding display_name column to users table...")

    try:
        with engine.connect() as connection:
            # Check if column already exists
            result = connection.execute(
                text(
                    """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'display_name'
            """
                )
            )

            if result.fetchone():
                print("✅ display_name column already exists")
                return

            # Add the column
            print("Adding display_name column...")
            connection.execute(
                text(
                    """
                ALTER TABLE users
                ADD COLUMN display_name VARCHAR NULL
            """
                )
            )

            connection.commit()
            print("✅ display_name column added successfully")

    except Exception as e:
        print(f"❌ Failed to add display_name column: {e}")
        raise


def verify_column_added():
    """Verify that the column was added successfully"""
    print("\nVerifying column was added...")

    try:
        with engine.connect() as connection:
            result = connection.execute(
                text(
                    """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'display_name'
            """
                )
            )

            column = result.fetchone()
            if column:
                print(
                    f"✅ display_name column verified: {column[0]} ({column[1]}, nullable: {column[2]})"
                )
            else:
                print("❌ display_name column not found")

    except Exception as e:
        print(f"❌ Verification failed: {e}")


if __name__ == "__main__":
    add_display_name_column()
    verify_column_added()
