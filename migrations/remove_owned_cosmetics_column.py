import sys
import os
import logging
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run():
    with engine.connect() as connection:
        trans = connection.begin()
        try:
            logger.info("Removing redundant owned_cosmetics column from users table...")
            # Check if column exists before dropping
            check_query = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='users' AND column_name='owned_cosmetics'
            """)
            result = connection.execute(check_query).fetchone()
            
            if result:
                connection.execute(text(
                    "ALTER TABLE users DROP COLUMN owned_cosmetics"
                ))
                logger.info("Successfully removed owned_cosmetics column.")
            else:
                logger.info("owned_cosmetics column does not exist, skipping...")
            
            trans.commit()
            logger.info("Migration completed successfully.")
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            trans.rollback()
            raise

if __name__ == "__main__":
    run()

