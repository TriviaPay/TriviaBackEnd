import sys
import os
import logging
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_question_tracking():
    try:
        connection = engine.connect()
        trans = connection.begin()

        logger.info("Fixing question tracking logic...")

        # Add new columns to track user attempts separately from question allocation
        try:
            connection.execute(text("""
                ALTER TABLE trivia_questions_daily
                ADD COLUMN IF NOT EXISTS user_attempted BOOLEAN DEFAULT FALSE
            """))
            logger.info("✅ Added user_attempted column")
        except Exception as e:
            logger.warning(f"Could not add user_attempted column: {e}")

        try:
            connection.execute(text("""
                ALTER TABLE trivia_questions_daily
                ADD COLUMN IF NOT EXISTS user_answer VARCHAR
            """))
            logger.info("✅ Added user_answer column")
        except Exception as e:
            logger.warning(f"Could not add user_answer column: {e}")

        try:
            connection.execute(text("""
                ALTER TABLE trivia_questions_daily
                ADD COLUMN IF NOT EXISTS user_is_correct BOOLEAN
            """))
            logger.info("✅ Added user_is_correct column")
        except Exception as e:
            logger.warning(f"Could not add user_is_correct column: {e}")

        try:
            connection.execute(text("""
                ALTER TABLE trivia_questions_daily
                ADD COLUMN IF NOT EXISTS user_answered_at TIMESTAMP
            """))
            logger.info("✅ Added user_answered_at column")
        except Exception as e:
            logger.warning(f"Could not add user_answered_at column: {e}")

        # Reset is_used to false for all questions (they should only be true if allocated to users)
        try:
            connection.execute(text("""
                UPDATE trivia_questions_daily
                SET is_used = FALSE
            """))
            logger.info("✅ Reset is_used flags")
        except Exception as e:
            logger.warning(f"Could not reset is_used flags: {e}")

        trans.commit()
        logger.info("Successfully fixed question tracking")

    except Exception as e:
        trans.rollback()
        logger.error(f"Error fixing question tracking: {e}")
        raise
    finally:
        connection.close()

if __name__ == "__main__":
    fix_question_tracking()
