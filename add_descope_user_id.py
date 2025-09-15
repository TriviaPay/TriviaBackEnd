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

def add_descope_user_id_column():
    """Add the missing descope_user_id column to the users table"""
    print("Adding descope_user_id column to users table...")
    
    try:
        with engine.connect() as connection:
            # Check if column already exists
            result = connection.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'users' AND column_name = 'descope_user_id'
            """))
            
            if result.fetchone():
                print("✅ descope_user_id column already exists")
                return
            
            # Add the column
            print("Adding descope_user_id column...")
            connection.execute(text("""
                ALTER TABLE users 
                ADD COLUMN descope_user_id VARCHAR UNIQUE
            """))
            
            # Create index for better performance
            print("Creating index on descope_user_id...")
            connection.execute(text("""
                CREATE INDEX idx_users_descope_user_id ON users(descope_user_id)
            """))
            
            connection.commit()
            print("✅ descope_user_id column added successfully")
            
    except Exception as e:
        print(f"❌ Failed to add descope_user_id column: {e}")
        raise

def verify_column_added():
    """Verify that the column was added successfully"""
    print("\nVerifying column was added...")
    
    try:
        with engine.connect() as connection:
            result = connection.execute(text("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns 
                WHERE table_name = 'users' AND column_name = 'descope_user_id'
            """))
            
            column = result.fetchone()
            if column:
                print(f"✅ descope_user_id column verified: {column[0]} ({column[1]}, nullable: {column[2]})")
            else:
                print("❌ descope_user_id column not found")
                
    except Exception as e:
        print(f"❌ Verification failed: {e}")

if __name__ == "__main__":
    add_descope_user_id_column()
    verify_column_added() 