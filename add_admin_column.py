import sys
import os

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text
from db import engine
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def upgrade():
    # Add is_admin column
    logger.info("Adding is_admin column to users table")
    with engine.connect() as conn:
        try:
            conn.execute(text("""
                ALTER TABLE users 
                ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;
            """))
            conn.commit()
            logger.info("Successfully added is_admin column")
        except Exception as e:
            logger.error(f"Error adding is_admin column: {e}")
            raise

def downgrade():
    # Remove is_admin column
    logger.info("Removing is_admin column from users table")
    with engine.connect() as conn:
        try:
            conn.execute(text("""
                ALTER TABLE users 
                DROP COLUMN IF EXISTS is_admin;
            """))
            conn.commit()
            logger.info("Successfully removed is_admin column")
        except Exception as e:
            logger.error(f"Error removing is_admin column: {e}")
            raise

if __name__ == "__main__":
    upgrade() 