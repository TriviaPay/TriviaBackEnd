#!/usr/bin/env python3

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import engine
from sqlalchemy import text

def check_database_schema():
    """Check the actual database schema"""
    print("Checking actual database schema...")
    
    try:
        with engine.connect() as connection:
            # Get all tables
            result = connection.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
                ORDER BY table_name
            """))
            tables = result.fetchall()
            
            print(f"Found {len(tables)} tables:")
            for table in tables:
                print(f"  - {table[0]}")
            
            # Check users table specifically
            print("\nUsers table columns:")
            result = connection.execute(text("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns 
                WHERE table_name = 'users' 
                ORDER BY ordinal_position
            """))
            columns = result.fetchall()
            
            for col in columns:
                print(f"  - {col[0]}: {col[1]} (nullable: {col[2]}, default: {col[3]})")
            
            # Check if descope_user_id exists
            descope_exists = any(col[0] == 'descope_user_id' for col in columns)
            if descope_exists:
                print("\n✅ descope_user_id column exists")
            else:
                print("\n❌ descope_user_id column missing")
                
            # Check if account_id exists
            account_id_exists = any(col[0] == 'account_id' for col in columns)
            if account_id_exists:
                print("✅ account_id column exists")
            else:
                print("❌ account_id column missing")
                
    except Exception as e:
        print(f"❌ Database schema check failed: {e}")

if __name__ == "__main__":
    check_database_schema() 