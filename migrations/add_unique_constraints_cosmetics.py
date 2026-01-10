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
            logger.info("Adding unique constraints for idempotent cosmetic purchases...")

            # Check if constraint already exists for user_avatars
            check_avatar_constraint = text("""
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_user_avatar'
            """)
            result = connection.execute(check_avatar_constraint)
            if not result.fetchone():
                logger.info("Creating unique constraint on user_avatars (user_id, avatar_id)...")
                connection.execute(text(
                    """
                    ALTER TABLE user_avatars
                      ADD CONSTRAINT uq_user_avatar UNIQUE (user_id, avatar_id);
                    """
                ))
                logger.info("Unique constraint created on user_avatars.")
            else:
                logger.info("Unique constraint already exists on user_avatars.")

            # Check if constraint already exists for user_frames
            check_frame_constraint = text("""
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_user_frame'
            """)
            result = connection.execute(check_frame_constraint)
            if not result.fetchone():
                logger.info("Creating unique constraint on user_frames (user_id, frame_id)...")
                connection.execute(text(
                    """
                    ALTER TABLE user_frames
                      ADD CONSTRAINT uq_user_frame UNIQUE (user_id, frame_id);
                    """
                ))
                logger.info("Unique constraint created on user_frames.")
            else:
                logger.info("Unique constraint already exists on user_frames.")

            trans.commit()
            logger.info("Migration completed successfully.")
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            trans.rollback()
            raise

if __name__ == "__main__":
    run()
