import sys
import os
import logging
from sqlalchemy import text

# Add the parent directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def drop_columns_if_exist():
    try:
        connection = engine.connect()

        # Helper to check if column exists
        def column_exists(table: str, column: str) -> bool:
            check_query = text("""
                SELECT 1 FROM information_schema.columns 
                WHERE table_name=:table AND column_name=:column
            """)
            result = connection.execute(check_query, {"table": table, "column": column})
            return result.fetchone() is not None

        # Drop users.sub if exists
        if column_exists('users', 'sub'):
            logger.info("Dropping column 'sub' from users table")
            connection.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS sub"))
        else:
            logger.info("Column 'sub' not present, skipping")

        # Drop users.refresh_token if exists
        if column_exists('users', 'refresh_token'):
            logger.info("Dropping column 'refresh_token' from users table")
            connection.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS refresh_token"))
        else:
            logger.info("Column 'refresh_token' not present, skipping")

        connection.commit()
        connection.close()
        print("Column drop migration completed")
        return True
    except Exception as e:
        logger.error(f"Error dropping columns: {str(e)}")
        print(f"Error dropping columns: {str(e)}")
        return False

if __name__ == "__main__":
    logger.info("Starting migration to drop 'sub' and 'refresh_token' columns")
    print("Starting migration to drop 'sub' and 'refresh_token' columns")
    success = drop_columns_if_exist()
    if success:
        logger.info("Migration completed successfully")
        print("Migration completed successfully")
    else:
        logger.error("Migration failed")
        print("Migration failed") 