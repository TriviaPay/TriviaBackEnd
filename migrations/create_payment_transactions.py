import sys
import os
import logging
from sqlalchemy import text, inspect
from datetime import datetime

# Add the parent directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Now import from the main app
from db import get_db, engine
from models import PaymentTransaction

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_payment_transactions_table():
    """
    Create the payment_transactions table if it doesn't exist.
    """
    try:
        # Create a DB session
        db = next(get_db())
        
        # Check if table exists - create it if not
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        
        if "payment_transactions" not in tables:
            logger.info("Creating payment_transactions table")
            PaymentTransaction.__table__.create(engine, checkfirst=True)
            logger.info("Created payment_transactions table")
            return True
        else:
            logger.info("payment_transactions table already exists, skipping")
            return True
    except Exception as e:
        logger.error(f"Error creating payment_transactions table: {str(e)}")
        return False

if __name__ == "__main__":
    logger.info("Starting migration to create payment_transactions table")
    print("Starting migration to create payment_transactions table")
    success = create_payment_transactions_table()
    if success:
        logger.info("Migration completed successfully")
        print("Migration completed successfully")
    else:
        logger.error("Migration failed")
        print("Migration failed") 