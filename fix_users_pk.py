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

def fix_users_primary_key():
    print("Fixing users table primary key...")
    try:
        with engine.connect() as connection:
            # 1. Find the primary key constraint name for account_id
            result = connection.execute(text("""
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_name = 'users' AND constraint_type = 'PRIMARY KEY'
            """))
            pk_row = result.fetchone()
            if not pk_row:
                print("No primary key constraint found on users table.")
            else:
                pk_name = pk_row[0]
                print(f"Dropping primary key constraint: {pk_name}")
                connection.execute(text(f"ALTER TABLE users DROP CONSTRAINT {pk_name}"))
            # 2. Make account_id unique and not null (if not already)
            print("Adding unique constraint to account_id (if not exists)...")
            connection.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.table_constraints
                        WHERE table_name = 'users' AND constraint_type = 'UNIQUE' AND constraint_name = 'users_account_id_key'
                    ) THEN
                        ALTER TABLE users ADD CONSTRAINT users_account_id_key UNIQUE(account_id);
                    END IF;
                END$$;
            """))
            # 3. Set id as primary key (if not already)
            print("Setting id as primary key (if not already)...")
            connection.execute(text("""
                ALTER TABLE users ADD PRIMARY KEY (id)
            """))
            connection.commit()
            print("✅ users table primary key fixed: id is now the primary key, account_id is unique.")
    except Exception as e:
        print(f"❌ Failed to fix users table primary key: {e}")
        raise

def verify_pk():
    print("\nVerifying primary key and unique constraints...")
    try:
        with engine.connect() as connection:
            result = connection.execute(text("""
                SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS data_type, i.indisprimary, i.indisunique
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = 'users'::regclass
            """))
            for row in result.fetchall():
                print(f"  - {row[0]}: {row[1]}, primary: {row[2]}, unique: {row[3]}")
    except Exception as e:
        print(f"❌ Verification failed: {e}")

if __name__ == "__main__":
    fix_users_primary_key()
    verify_pk() 