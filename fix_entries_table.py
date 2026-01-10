import logging

from sqlalchemy import text

from db import SessionLocal

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def fix_entries_table():
    """
    Add the missing number_of_entries column to the entries table
    """
    db = SessionLocal()
    try:
        # Check if column exists
        logger.info("Checking if number_of_entries column exists in entries table...")
        result = db.execute(
            text(
                """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'entries'
            AND column_name = 'number_of_entries'
        )
        """
            )
        )
        column_exists = result.scalar()

        if not column_exists:
            # Add the missing column
            logger.info("Adding number_of_entries column to entries table...")
            db.execute(
                text(
                    """
            ALTER TABLE entries ADD COLUMN number_of_entries INTEGER;
            UPDATE entries SET number_of_entries = 0;
            ALTER TABLE entries ALTER COLUMN number_of_entries SET NOT NULL;
            """
                )
            )
            db.commit()
            logger.info("Column added successfully!")
        else:
            logger.info("Column already exists, no action needed.")

        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Error fixing entries table: {str(e)}")
        return False
    finally:
        db.close()


if __name__ == "__main__":
    success = fix_entries_table()
    if success:
        print("Entries table fixed successfully.")
    else:
        print("Failed to fix entries table.")
