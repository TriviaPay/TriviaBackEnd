import sys
import os
import logging
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def remove_redundant_columns():
    try:
        connection = engine.connect()
        trans = connection.begin()

        logger.info("Removing redundant columns from users table...")

        # Remove is_referred column (can be inferred from referred_by IS NOT NULL)
        try:
            connection.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS is_referred"))
            logger.info("✅ Removed is_referred column")
        except Exception as e:
            logger.warning(f"Could not remove is_referred column: {e}")

        # Remove display_name column (can be inferred from username or other name fields)
        try:
            connection.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS display_name"))
            logger.info("✅ Removed display_name column")
        except Exception as e:
            logger.warning(f"Could not remove display_name column: {e}")

        trans.commit()
        logger.info("Successfully removed redundant columns")

    except Exception as e:
        trans.rollback()
        logger.error(f"Error removing redundant columns: {e}")
        raise
    finally:
        connection.close()

if __name__ == "__main__":
    remove_redundant_columns()
