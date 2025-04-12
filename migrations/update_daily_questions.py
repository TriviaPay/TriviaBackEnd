import os
import sys
import psycopg2

# Add parent directory to path so we can import db
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import DATABASE_URL

def update_daily_questions_table():
    """Add answer tracking fields to daily_questions table."""
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cursor = conn.cursor()

    try:
        # Add answer column if it doesn't exist
        cursor.execute("""
        DO $$ 
        BEGIN 
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'daily_questions' AND column_name = 'answer'
            ) THEN 
                ALTER TABLE daily_questions ADD COLUMN answer VARCHAR;
            END IF;
        END $$;
        """)

        # Add is_correct column if it doesn't exist
        cursor.execute("""
        DO $$ 
        BEGIN 
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'daily_questions' AND column_name = 'is_correct'
            ) THEN 
                ALTER TABLE daily_questions ADD COLUMN is_correct BOOLEAN;
            END IF;
        END $$;
        """)

        # Add answered_at column if it doesn't exist
        cursor.execute("""
        DO $$ 
        BEGIN 
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'daily_questions' AND column_name = 'answered_at'
            ) THEN 
                ALTER TABLE daily_questions ADD COLUMN answered_at TIMESTAMP;
            END IF;
        END $$;
        """)

        conn.commit()
        print("Successfully updated daily_questions table!")

    except Exception as e:
        # Rollback in case of error
        conn.rollback()
        print(f"Error updating daily_questions table: {e}")
    finally:
        # Close the connection
        cursor.close()
        conn.close()

if __name__ == "__main__":
    update_daily_questions_table() 