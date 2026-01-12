import sys
import os
import logging
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_share_fields_to_liveupdates():
    try:
        connection = engine.connect()
        trans = connection.begin()

        logger.info("Adding share fields to liveupdates table...")

        # Add share_text column
        connection.execute(text("""
            ALTER TABLE liveupdates
            ADD COLUMN IF NOT EXISTS share_text VARCHAR
        """))

        # Add app_link column
        connection.execute(text("""
            ALTER TABLE liveupdates
            ADD COLUMN IF NOT EXISTS app_link VARCHAR
        """))

        trans.commit()
        logger.info("Successfully added share fields to liveupdates table")

    except Exception as e:
        trans.rollback()
        logger.error(f"Error adding share fields to liveupdates table: {e}")
        raise
    finally:
        connection.close()

if __name__ == "__main__":
    add_share_fields_to_liveupdates()
