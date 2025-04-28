#!/usr/bin/env python3
import sys
import os
import logging
from sqlalchemy import create_engine, MetaData, text

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add the parent directory to sys.path to import from the project
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config import DATABASE_URL
except ImportError:
    logger.error("Could not import DATABASE_URL from config. Check that config.py exists and contains this variable.")
    sys.exit(1)

def update_payment_transactions_table():
    """
    Update the payment_transactions table to add admin_notes column
    and make payment_intent_id nullable.
    """
    try:
        # Check if DATABASE_URL is provided
        if not DATABASE_URL:
            logger.error("DATABASE_URL is not set in config or environment variables.")
            return False
            
        # Connect to the database
        engine = create_engine(DATABASE_URL)
        metadata = MetaData()
        metadata.reflect(bind=engine)
        
        # Check if the payment_transactions table exists
        if 'payment_transactions' not in metadata.tables:
            logger.error("payment_transactions table does not exist!")
            return False
        
        # Get the payment_transactions table
        payment_transactions = metadata.tables['payment_transactions']
        
        with engine.connect() as conn:
            # Check if admin_notes column already exists
            if 'admin_notes' not in payment_transactions.columns:
                # Add admin_notes column
                logger.info("Adding admin_notes column to payment_transactions table...")
                conn.execute(text("ALTER TABLE payment_transactions ADD COLUMN admin_notes VARCHAR;"))
                conn.commit()
                logger.info("admin_notes column added successfully.")
            else:
                logger.info("admin_notes column already exists in payment_transactions table.")
            
            # Check current nullability of payment_intent_id
            result = conn.execute(text("SELECT is_nullable FROM information_schema.columns WHERE table_name = 'payment_transactions' AND column_name = 'payment_intent_id';"))
            is_nullable = result.scalar()
            
            if is_nullable == 'NO':  # Column is NOT NULL
                # Make payment_intent_id nullable
                logger.info("Updating payment_intent_id to be nullable...")
                conn.execute(text("ALTER TABLE payment_transactions ALTER COLUMN payment_intent_id DROP NOT NULL;"))
                conn.commit()
                logger.info("payment_intent_id updated to be nullable.")
            else:
                logger.info("payment_intent_id is already nullable.")
        
        logger.info("Migration completed successfully!")
        return True
    
    except Exception as e:
        logger.error(f"Error updating payment_transactions table: {str(e)}")
        return False

if __name__ == "__main__":
    logger.info("Starting migration to update payment_transactions table...")
    success = update_payment_transactions_table()
    
    if success:
        logger.info("Migration completed successfully!")
        sys.exit(0)
    else:
        logger.error("Migration failed!")
        sys.exit(1) 