"""
Script to fix the daily_questions table by renaming user_id to account_id.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import text

from db import engine  # Import the existing engine from your db module

# Use the existing engine that's already configured with the correct URL
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def fix_daily_questions():
    """Rename user_id column to account_id in daily_questions table"""
    try:
        # Check if user_id column exists
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='daily_questions' AND column_name='user_id')"
                )
            )
            user_id_exists = result.scalar()

            result = conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='daily_questions' AND column_name='account_id')"
                )
            )
            account_id_exists = result.scalar()

            if user_id_exists and not account_id_exists:
                print(
                    "Renaming user_id column to account_id in daily_questions table..."
                )
                conn.execute(
                    text(
                        "ALTER TABLE daily_questions RENAME COLUMN user_id TO account_id"
                    )
                )
                print("Column renamed successfully!")
            elif account_id_exists:
                print("account_id column already exists in daily_questions table.")
            else:
                print("user_id column does not exist in daily_questions table.")

            # Commit the transaction
            conn.commit()
    except Exception as e:
        print(f"Error fixing daily_questions table: {str(e)}")


if __name__ == "__main__":
    fix_daily_questions()
