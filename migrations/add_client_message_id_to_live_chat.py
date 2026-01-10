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
            logger.info("Adding client_message_id column to live_chat_messages...")

            # Add client_message_id column if it doesn't exist
            connection.execute(text(
                """
                ALTER TABLE live_chat_messages
                  ADD COLUMN IF NOT EXISTS client_message_id VARCHAR;
                """
            ))

            logger.info("Creating unique constraint for idempotent writes...")

            # Create unique constraint for idempotent writes
            # First check if constraint already exists
            check_constraint = text("""
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_client_message_id'
            """)
            result = connection.execute(check_constraint)
            if not result.fetchone():
                # Add unique constraint (allows NULLs, but enforces uniqueness when provided)
                connection.execute(text(
                    """
                    CREATE UNIQUE INDEX uq_client_message_id
                    ON live_chat_messages (session_id, user_id, client_message_id)
                    WHERE client_message_id IS NOT NULL;
                    """
                ))
                logger.info("Unique constraint created successfully.")
            else:
                logger.info("Unique constraint already exists.")

            trans.commit()
            logger.info("Migration completed successfully.")
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            trans.rollback()
            raise

if __name__ == "__main__":
    run()
