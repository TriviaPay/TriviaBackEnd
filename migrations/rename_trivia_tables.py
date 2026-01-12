import sys
import os
import logging
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def rename_trivia_tables():
    try:
        connection = engine.connect()
        trans = connection.begin()

        logger.info("Renaming trivia-related tables...")

        # Rename tables
        renames = [
            ("daily_questions", "trivia_questions_daily"),
            ("entries", "trivia_questions_entries"),
            ("user_question_answers", "trivia_questions_answers"),
            ("trivia_draw_winners", "trivia_questions_winners")
        ]

        for old_name, new_name in renames:
            try:
                connection.execute(text(f"ALTER TABLE {old_name} RENAME TO {new_name}"))
                logger.info(f"✅ Renamed {old_name} to {new_name}")
            except Exception as e:
                logger.warning(f"Could not rename {old_name}: {e}")

        trans.commit()
        logger.info("Successfully renamed trivia tables")

    except Exception as e:
        trans.rollback()
        logger.error(f"Error renaming tables: {e}")
        raise
    finally:
        connection.close()

if __name__ == "__main__":
    rename_trivia_tables()
