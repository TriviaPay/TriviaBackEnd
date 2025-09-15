#!/usr/bin/env python3
"""
Migration script to add stripe_customer_id column to users table
"""

import sys
import os
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# Add the parent directory to the path so we can import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_stripe_customer_id_column():
    """Add stripe_customer_id column to users table"""
    
    # Check if DATABASE_URL is configured
    if not config.DATABASE_URL:
        raise ValueError("DATABASE_URL is not configured in config.py")
    
    # Create database engine
    engine = create_engine(config.DATABASE_URL)
    
    try:
        with engine.connect() as conn:
            # Check if the column already exists
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'users' 
                AND column_name = 'stripe_customer_id'
            """))
            
            column_exists = result.fetchone() is not None
            
            if not column_exists:
                # Add the stripe_customer_id column
                conn.execute(text("""
                    ALTER TABLE users 
                    ADD COLUMN stripe_customer_id VARCHAR;
                """))
                
                # Create an index on the column for better query performance
                conn.execute(text("""
                    CREATE INDEX idx_users_stripe_customer_id 
                    ON users(stripe_customer_id);
                """))
                
                conn.commit()
                logger.info("Successfully added stripe_customer_id column to users table")
            else:
                logger.info("stripe_customer_id column already exists in users table")
                
    except SQLAlchemyError as e:
        logger.error(f"Database error: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise

if __name__ == "__main__":
    logger.info("Starting migration to add stripe_customer_id column...")
    add_stripe_customer_id_column()
    logger.info("Migration completed successfully!") 