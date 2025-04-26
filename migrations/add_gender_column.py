import sys
import os
import logging
from sqlalchemy import text

# Add the parent directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Now import from the main app
from db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_gender_column():
    """
    Add the gender column to the users table if it doesn't exist.
    """
    try:
        # Create a connection to the database
        connection = engine.connect()
        
        # Check if the column already exists using text() for SQL execution
        check_query = text("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='gender'")
        result = connection.execute(check_query)
        column_exists = result.fetchone() is not None
        
        if not column_exists:
            logger.info("Adding gender column to users table")
            # Use text() for SQL execution
            alter_query = text("ALTER TABLE users ADD COLUMN gender VARCHAR")
            connection.execute(alter_query)
            connection.commit()
            logger.info("Successfully added gender column")
        else:
            logger.info("gender column already exists, skipping")
            
        connection.close()
        print("Gender column check completed")
        return True
    except Exception as e:
        logger.error(f"Error adding gender column: {str(e)}")
        print(f"Error adding gender column: {str(e)}")
        return False

if __name__ == "__main__":
    logger.info("Starting migration to add gender field")
    print("Starting migration to add gender field")
    success = add_gender_column()
    if success:
        logger.info("Migration completed successfully")
        print("Migration completed successfully")
    else:
        logger.error("Migration failed")
        print("Migration failed") 