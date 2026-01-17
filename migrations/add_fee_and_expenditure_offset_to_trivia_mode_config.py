import sys
import os
import logging
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def add_fee_and_expenditure_offset_to_trivia_mode_config():
    try:
        connection = engine.connect()
        trans = connection.begin()

        logger.info("Adding fee_per_user and expenditure_offset columns to trivia_mode_config if missing...")

        check_fee_query = text(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name='trivia_mode_config' AND column_name='fee_per_user'
            """
        )
        fee_result = connection.execute(check_fee_query)
        if not fee_result.fetchone():
            logger.info("Adding fee_per_user column to trivia_mode_config")
            connection.execute(
                text(
                    """
                    ALTER TABLE trivia_mode_config
                    ADD COLUMN fee_per_user FLOAT NOT NULL DEFAULT 0.0
                    """
                )
            )
        else:
            logger.info("fee_per_user column already exists")

        check_offset_query = text(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name='trivia_mode_config' AND column_name='expenditure_offset'
            """
        )
        offset_result = connection.execute(check_offset_query)
        if not offset_result.fetchone():
            logger.info("Adding expenditure_offset column to trivia_mode_config")
            connection.execute(
                text(
                    """
                    ALTER TABLE trivia_mode_config
                    ADD COLUMN expenditure_offset INTEGER NOT NULL DEFAULT 0
                    """
                )
            )
        else:
            logger.info("expenditure_offset column already exists")

        logger.info("Backfilling bronze defaults for fee_per_user and expenditure_offset")
        connection.execute(
            text(
                """
                UPDATE trivia_mode_config
                SET fee_per_user = 1.0
                WHERE mode_id = 'bronze' AND fee_per_user = 0.0
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE trivia_mode_config
                SET expenditure_offset = 200
                WHERE mode_id = 'bronze' AND expenditure_offset = 0
                """
            )
        )

        trans.commit()
        connection.close()
        logger.info("Migration completed successfully")
        return True
    except Exception as e:
        logger.error(f"Error adding fee_per_user/expenditure_offset: {str(e)}")
        if "trans" in locals():
            trans.rollback()
        if "connection" in locals():
            connection.close()
        return False


if __name__ == "__main__":
    logger.info("Starting fee_per_user/expenditure_offset migration")
    success = add_fee_and_expenditure_offset_to_trivia_mode_config()
    if success:
        print("Migration completed successfully")
    else:
        print("Migration failed")
