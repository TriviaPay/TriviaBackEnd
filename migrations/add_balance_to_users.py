import sys
import os
import logging
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

# Add the parent directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_db

def add_balance_column():
    """
    Add balance column to the users table if it doesn't exist yet.
    """
    # Configure logging to output to console
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )
    logger = logging.getLogger(__name__)
    
    print("Starting migration to add balance column to users table...")
    logger.info("Starting migration to add balance column to users table...")
    
    # Get database connection
    db = next(get_db())
    
    try:
        # Check if the column already exists
        check_query = text("""
            SELECT EXISTS (
                SELECT 1 
                FROM information_schema.columns 
                WHERE table_name='users' AND column_name='balance'
            );
        """)
        
        column_exists = db.execute(check_query).scalar()
        
        if column_exists:
            print("Balance column already exists in users table. Skipping migration.")
            logger.info("Balance column already exists in users table. Skipping migration.")
            return
        
        # Check if the wallet_balance column exists
        wallet_balance_check = text("""
            SELECT EXISTS (
                SELECT 1 
                FROM information_schema.columns 
                WHERE table_name='users' AND column_name='wallet_balance'
            );
        """)
        
        wallet_balance_exists = db.execute(wallet_balance_check).scalar()
        print(f"wallet_balance column exists: {wallet_balance_exists}")
        
        # Add the balance column with default value of 0
        add_column_query = text("""
            ALTER TABLE users 
            ADD COLUMN balance FLOAT DEFAULT 0.0;
        """)
        
        db.execute(add_column_query)
        db.commit()
        
        print("Successfully added balance column to users table!")
        logger.info("Successfully added balance column to users table!")
        
    except SQLAlchemyError as e:
        db.rollback()
        error_msg = f"Error during migration: {str(e)}"
        print(error_msg)
        logger.error(error_msg)
        raise
    finally:
        db.close()

if __name__ == "__main__":
    add_balance_column() 