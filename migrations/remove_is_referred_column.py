import sys
import os
import logging
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def remove_is_referred_column():
    try:
        connection = engine.connect()
        trans = connection.begin()

        logger.info("Removing is_referred column from users table...")

        # Check if column exists first
        result = connection.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'users' AND column_name = 'is_referred'
        """))

        if result.fetchone():
            # Column exists, remove it
            connection.execute(text("""
                ALTER TABLE users
                DROP COLUMN is_referred
            """))
            logger.info("Successfully removed is_referred column")
        else:
            logger.info("is_referred column does not exist, nothing to remove")

        trans.commit()
        logger.info("Migration completed successfully")

    except Exception as e:
        trans.rollback()
        logger.error(f"Error removing is_referred column: {e}")
        raise
    finally:
        connection.close()

if __name__ == "__main__":
    remove_is_referred_column()
