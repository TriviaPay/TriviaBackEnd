import sys
import os
import logging
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def add_prize_pool_share_to_trivia_mode_config():
    try:
        connection = engine.connect()
        trans = connection.begin()

        logger.info("Adding prize_pool_share column to trivia_mode_config if missing...")

        check_column_query = text(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name='trivia_mode_config' AND column_name='prize_pool_share'
            """
        )
        result = connection.execute(check_column_query)
        if not result.fetchone():
            logger.info("Adding prize_pool_share column to trivia_mode_config")
            connection.execute(
                text(
                    """
                    ALTER TABLE trivia_mode_config
                    ADD COLUMN prize_pool_share FLOAT NOT NULL DEFAULT 0.005
                    """
                )
            )
        else:
            logger.info("prize_pool_share column already exists")

        trans.commit()
        connection.close()
        logger.info("Migration completed successfully")
        return True
    except Exception as e:
        logger.error(f"Error adding prize_pool_share: {str(e)}")
        if "trans" in locals():
            trans.rollback()
        if "connection" in locals():
            connection.close()
        return False


if __name__ == "__main__":
    logger.info("Starting prize_pool_share migration")
    success = add_prize_pool_share_to_trivia_mode_config()
    if success:
        print("Migration completed successfully")
    else:
        print("Migration failed")
