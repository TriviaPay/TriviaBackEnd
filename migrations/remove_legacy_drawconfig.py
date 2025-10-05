#!/usr/bin/env python3
"""
Migration script to remove legacy DrawConfig table and related code.
This removes the winners_draw_settings table since it's been replaced by TriviaDrawConfig.
"""

import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Add the parent directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_URL

def remove_legacy_drawconfig():
    """Remove the legacy winners_draw_settings table."""
    
    # Create database connection
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    
    try:
        print("Starting migration: Remove legacy DrawConfig table...")
        
        # Check if the table exists
        result = db.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'winners_draw_settings'
            );
        """))
        
        table_exists = result.scalar()
        
        if table_exists:
            print("Found winners_draw_settings table. Dropping it...")
            
            # Drop the table
            db.execute(text("DROP TABLE IF EXISTS winners_draw_settings CASCADE;"))
            db.commit()
            
            print("✅ Successfully dropped winners_draw_settings table")
        else:
            print("ℹ️  winners_draw_settings table does not exist. Nothing to do.")
        
        print("Migration completed successfully!")
        
    except Exception as e:
        print(f"❌ Error during migration: {e}")
        db.rollback()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    remove_legacy_drawconfig()
