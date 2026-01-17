import os
import sys
import logging
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def add_faqs_table():
    try:
        connection = engine.connect()
        trans = connection.begin()

        logger.info("Creating faqs table if missing...")

        check_table_query = text(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_name='faqs'
            """
        )
        result = connection.execute(check_table_query)
        if not result.fetchone():
            logger.info("Creating faqs table")
            connection.execute(
                text(
                    """
                    CREATE TABLE faqs (
                        id SERIAL PRIMARY KEY,
                        question TEXT NOT NULL,
                        answer TEXT NOT NULL,
                        created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL,
                        updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    CREATE INDEX ix_faqs_id ON faqs (id)
                    """
                )
            )
        else:
            logger.info("faqs table already exists")

        trans.commit()
        connection.close()
        logger.info("FAQ migration completed successfully")
        return True
    except Exception as e:
        logger.error(f"Error creating faqs table: {str(e)}")
        if "trans" in locals():
            trans.rollback()
        if "connection" in locals():
            connection.close()
        return False


if __name__ == "__main__":
    logger.info("Starting faqs table migration")
    success = add_faqs_table()
    if success:
        print("Migration completed successfully")
    else:
        print("Migration failed")
