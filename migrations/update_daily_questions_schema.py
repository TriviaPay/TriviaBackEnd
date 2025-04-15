import logging
from sqlalchemy import create_engine, text
from db import engine  # Use the existing engine from db.py

def update_daily_questions_schema():
    """
    Update the daily_questions table to remove user relationship and add correct_answer column.
    This is a major schema change - make sure to back up data first!
    """
    logger = logging.getLogger(__name__)
    
    try:
        # Create a temporary backup table
        with engine.connect() as conn:
            logger.info("Creating backup of daily_questions table")
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS daily_questions_backup AS 
                SELECT * FROM daily_questions
            """))
            
            # Drop existing table constraints and relationships
            logger.info("Removing existing constraints from daily_questions")
            conn.execute(text("""
                DROP TABLE IF EXISTS daily_questions CASCADE;
            """))
            
            # Create new table structure
            logger.info("Creating new daily_questions table structure")
            conn.execute(text("""
                CREATE TABLE daily_questions (
                    id SERIAL PRIMARY KEY,
                    question_number INTEGER NOT NULL REFERENCES trivia(question_number),
                    date DATE NOT NULL,
                    is_common BOOLEAN DEFAULT FALSE,
                    question_order INTEGER NOT NULL,
                    is_used BOOLEAN DEFAULT FALSE,
                    was_changed BOOLEAN DEFAULT FALSE,
                    correct_answer VARCHAR,
                    UNIQUE(date, question_number)
                );
            """))
            
            logger.info("Daily questions table schema updated successfully")
            
    except Exception as e:
        logger.error(f"Error updating daily_questions schema: {str(e)}")
        raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    update_daily_questions_schema() 