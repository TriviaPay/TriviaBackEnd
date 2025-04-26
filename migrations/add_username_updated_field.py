import sys
import os
import logging
from sqlalchemy import create_engine, Column, Boolean, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Add the parent directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Now import from the main app
from db import get_db, Base, engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_username_updated_field():
    """
    Add the username_updated column to the users table.
    """
    try:
        # Add column to the table - we need to use raw SQL for this
        connection = engine.connect()
        
        # Check if the column already exists using text() for SQL execution
        check_query = text("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='username_updated'")
        result = connection.execute(check_query)
        column_exists = result.fetchone() is not None
        
        if not column_exists:
            logger.info("Adding username_updated column to users table")
            # Use text() for SQL execution
            alter_query = text("ALTER TABLE users ADD COLUMN username_updated BOOLEAN DEFAULT FALSE")
            connection.execute(alter_query)
            logger.info("Successfully added username_updated column")
        else:
            logger.info("username_updated column already exists, skipping")
            
        connection.close()
        
        return True
    except Exception as e:
        logger.error(f"Error adding username_updated column: {str(e)}")
        return False

if __name__ == "__main__":
    logger.info("Starting migration to add username_updated field")
    success = add_username_updated_field()
    if success:
        logger.info("Migration completed successfully")
    else:
        logger.error("Migration failed") 