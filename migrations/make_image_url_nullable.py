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
            logger.info("Making image_url nullable in avatars table...")
            connection.execute(text(
                "ALTER TABLE avatars ALTER COLUMN image_url DROP NOT NULL"
            ))
            
            logger.info("Making image_url nullable in frames table...")
            connection.execute(text(
                "ALTER TABLE frames ALTER COLUMN image_url DROP NOT NULL"
            ))
            
            trans.commit()
            logger.info("Migration completed successfully.")
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            trans.rollback()
            raise

if __name__ == "__main__":
    run()

