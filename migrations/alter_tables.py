import os
import sys
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Add parent directory to path so we can import db
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import DATABASE_URL

def update_account_id_to_bigint():
    """Update all tables to use BIGINT for account_id."""
    conn = psycopg2.connect(DATABASE_URL)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cursor = conn.cursor()

    try:
        print("Starting database schema update...")
        
        # List of tables and columns to modify
        tables_to_update = [
            {"table": "entries", "column": "account_id", "fk_table": "users", "pk": True},
            {"table": "winners", "column": "account_id", "fk_table": "users", "pk": True},
            {"table": "payment", "column": "account_id", "fk_table": "users", "pk": True},
            {"table": "comments", "column": "account_id", "fk_table": "users", "pk": False},
            {"table": "withdrawals", "column": "account_id", "fk_table": "users", "pk": False},
            {"table": "chats", "column": "sender_account_id", "fk_table": "users", "pk": False},
            {"table": "chats", "column": "receiver_account_id", "fk_table": "users", "pk": False},
            {"table": "user_avatars", "column": "user_id", "fk_table": "users", "pk": False},
            {"table": "user_frames", "column": "user_id", "fk_table": "users", "pk": False}
        ]
        
        for table_info in tables_to_update:
            table = table_info["table"]
            column = table_info["column"]
            fk_table = table_info["fk_table"]
            is_pk = table_info["pk"]
            
            print(f"Processing {table}.{column}...")
            
            # Check if table exists
            cursor.execute(f"""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = '{table}'
            );
            """)
            
            table_exists = cursor.fetchone()[0]
            
            if not table_exists:
                print(f"Table {table} does not exist. Skipping.")
                continue
            
            # Get constraint names for foreign key
            cursor.execute(f"""
            SELECT tc.constraint_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.constraint_column_usage AS ccu ON tc.constraint_name = ccu.constraint_name
            WHERE tc.table_name = '{table}' AND tc.constraint_type = 'FOREIGN KEY' AND ccu.column_name = '{column}';
            """)
            
            fk_constraints = cursor.fetchall()
            
            # Get primary key constraint if it exists
            if is_pk:
                cursor.execute(f"""
                SELECT tc.constraint_name
                FROM information_schema.table_constraints AS tc
                WHERE tc.table_name = '{table}' AND tc.constraint_type = 'PRIMARY KEY';
                """)
                
                pk_constraint = cursor.fetchone()
            else:
                pk_constraint = None
            
            # Drop foreign key constraints
            for fk in fk_constraints:
                cursor.execute(f"""
                ALTER TABLE {table} DROP CONSTRAINT {fk[0]};
                """)
                print(f"Dropped foreign key constraint: {fk[0]}")
            
            # Drop primary key constraint if it exists
            if pk_constraint:
                cursor.execute(f"""
                ALTER TABLE {table} DROP CONSTRAINT {pk_constraint[0]};
                """)
                print(f"Dropped primary key constraint: {pk_constraint[0]}")
            
            # Alter the column type
            cursor.execute(f"""
            ALTER TABLE {table} ALTER COLUMN {column} TYPE BIGINT;
            """)
            print(f"Changed {table}.{column} to BIGINT")
            
            # Recreate primary key if needed
            if is_pk:
                cursor.execute(f"""
                ALTER TABLE {table} ADD PRIMARY KEY ({column});
                """)
                print(f"Recreated primary key on {table}.{column}")
            
            # Recreate foreign key constraints
            for fk in fk_constraints:
                cursor.execute(f"""
                ALTER TABLE {table} ADD CONSTRAINT {fk[0]} 
                FOREIGN KEY ({column}) REFERENCES {fk_table}(account_id);
                """)
                print(f"Recreated foreign key constraint: {fk[0]}")
        
        print("Successfully updated all account_id columns to BIGINT!")

    except Exception as e:
        print(f"Error updating database schema: {e}")
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    update_account_id_to_bigint() 