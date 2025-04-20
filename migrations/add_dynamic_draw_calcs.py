import os
import sys
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import json
from datetime import datetime

# Add parent directory to path so we can import db
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# For psycopg2, we need to extract the connection parameters from the SQLAlchemy URL
def get_connection_string_from_db_url(db_url):
    # Remove SQLAlchemy driver prefix if present
    if '+' in db_url:
        db_url = db_url.split('+')[0] + db_url.split('+')[1].split('://', 1)[1]
    
    # Make sure it's a PostgreSQL connection
    if not db_url.startswith('postgresql://'):
        db_url = db_url.replace('postgres://', 'postgresql://')
    
    return db_url

def update_draw_config():
    """Add new columns to trivia_draw_config table for dynamic calculations."""
    # Get the database URL from environment or config
    db_url = os.environ.get('DATABASE_URL', config.DATABASE_URL)
    conn_string = get_connection_string_from_db_url(db_url)
    
    print(f"Connecting to database...")
    conn = psycopg2.connect(conn_string)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cursor = conn.cursor()

    try:
        print("Starting trivia_draw_config table update for dynamic calculations...")
        
        # Check if the table exists
        cursor.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'trivia_draw_config'
        );
        """)
        
        table_exists = cursor.fetchone()[0]
        
        if not table_exists:
            print("Table trivia_draw_config does not exist. Please run the initial migration first.")
            return
            
        # Check for draw_time_hour column as a marker 
        cursor.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'trivia_draw_config' AND column_name = 'draw_time_hour'
        );
        """)
        
        column_exists = cursor.fetchone()[0]
        
        if not column_exists:
            print("Adding explicit draw time columns...")
            cursor.execute("""
            ALTER TABLE trivia_draw_config 
            ADD COLUMN draw_time_hour INTEGER DEFAULT 23,
            ADD COLUMN draw_time_minute INTEGER DEFAULT 59,
            ADD COLUMN draw_timezone VARCHAR DEFAULT 'EST';
            """)
            
            # Extract values from custom_data if available
            print("Migrating time settings from custom_data...")
            cursor.execute("""
            SELECT id, custom_data FROM trivia_draw_config;
            """)
            
            configs = cursor.fetchall()
            for config_id, custom_data_str in configs:
                if custom_data_str:
                    try:
                        custom_data = json.loads(custom_data_str)
                        hour = custom_data.get('draw_time_hour', 20)
                        minute = custom_data.get('draw_time_minute', 00) 
                        timezone = custom_data.get('draw_timezone', 'EST')
                        
                        cursor.execute("""
                        UPDATE trivia_draw_config
                        SET draw_time_hour = %s, draw_time_minute = %s, draw_timezone = %s
                        WHERE id = %s;
                        """, (hour, minute, timezone, config_id))
                        
                        print(f"Migrated time settings for config ID {config_id}: {hour}:{minute} {timezone}")
                    except json.JSONDecodeError:
                        print(f"Failed to parse custom_data for config ID {config_id}")
            
            print("Successfully added draw time columns")
        else:
            print("Draw time columns already exist")
        
        # Check for calculated_pool_amount column
        cursor.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'trivia_draw_config' AND column_name = 'calculated_pool_amount'
        );
        """)
        
        column_exists = cursor.fetchone()[0]
        
        if not column_exists:
            print("Adding dynamic calculation columns...")
            cursor.execute("""
            ALTER TABLE trivia_draw_config 
            ADD COLUMN calculated_pool_amount FLOAT,
            ADD COLUMN calculated_winner_count INTEGER,
            ADD COLUMN last_calculation_time TIMESTAMP WITH TIME ZONE,
            ADD COLUMN use_dynamic_calculation BOOLEAN DEFAULT TRUE;
            """)
            
            print("Successfully added dynamic calculation columns")
        else:
            print("Dynamic calculation columns already exist")
        
        print("trivia_draw_config table update completed successfully!")

    except Exception as e:
        print(f"Error updating trivia_draw_config table: {e}")
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    update_draw_config() 