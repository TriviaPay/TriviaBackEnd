import sys
import os
import logging
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run():
    with engine.connect() as connection:
        trans = connection.begin()
        try:
            logger.info("Adding asset columns to avatars ...")
            connection.execute(text(
                """
                ALTER TABLE avatars
                  ADD COLUMN IF NOT EXISTS bucket VARCHAR,
                  ADD COLUMN IF NOT EXISTS object_key VARCHAR,
                  ADD COLUMN IF NOT EXISTS mime_type VARCHAR,
                  ADD COLUMN IF NOT EXISTS size_bytes BIGINT,
                  ADD COLUMN IF NOT EXISTS sha256 VARCHAR;
                """
            ))

            logger.info("Adding asset columns to frames ...")
            connection.execute(text(
                """
                ALTER TABLE frames
                  ADD COLUMN IF NOT EXISTS bucket VARCHAR,
                  ADD COLUMN IF NOT EXISTS object_key VARCHAR,
                  ADD COLUMN IF NOT EXISTS mime_type VARCHAR,
                  ADD COLUMN IF NOT EXISTS size_bytes BIGINT,
                  ADD COLUMN IF NOT EXISTS sha256 VARCHAR;
                """
            ))

            trans.commit()
            logger.info("Asset columns added successfully.")
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            trans.rollback()
            raise

if __name__ == "__main__":
    run()
