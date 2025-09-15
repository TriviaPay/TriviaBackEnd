#!/usr/bin/env python3

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import engine, SessionLocal
from models import User, Base
from sqlalchemy import text

def test_database_schema():
    """Test that the database schema is working correctly"""
    print("Testing database schema...")
    
    try:
        # Test database connection
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            print("✅ Database connection successful")
        
        # Test if tables exist
        with engine.connect() as connection:
            # Check if users table exists
            result = connection.execute(text("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'users' 
                ORDER BY ordinal_position
            """))
            columns = result.fetchall()
            
            if columns:
                print("✅ Users table exists with columns:")
                for col in columns:
                    print(f"  - {col[0]}: {col[1]}")
                
                # Check if account_id column exists
                account_id_exists = any(col[0] == 'account_id' for col in columns)
                if account_id_exists:
                    print("✅ account_id column exists in users table")
                else:
                    print("❌ account_id column missing from users table")
            else:
                print("❌ Users table does not exist")
        
        # Test creating a user (without committing)
        db = SessionLocal()
        try:
            test_user = User(
                descope_user_id="test_user_123",
                email="test@example.com",
                username="testuser",
                display_name="Test User"
            )
            
            # This should work without the account_id field causing issues
            print("✅ User model creation successful")
            
        except Exception as e:
            print(f"❌ User model creation failed: {e}")
        finally:
            db.close()
            
    except Exception as e:
        print(f"❌ Database test failed: {e}")

if __name__ == "__main__":
    test_database_schema() 