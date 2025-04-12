import os
import sys
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import json

# Add parent directory to path so we can import db
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import DATABASE_URL

def update_draw_config_table():
    """Add custom_data column to trivia_draw_config table."""
    conn = psycopg2.connect(DATABASE_URL)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cursor = conn.cursor()

    try:
        print("Starting trivia_draw_config table update...")
        
        # Check if the table exists
        cursor.execute("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'trivia_draw_config'
        );
        """)
        
        table_exists = cursor.fetchone()[0]
        
        if not table_exists:
            print("Table trivia_draw_config does not exist. Creating it with all required fields.")
            cursor.execute("""
            CREATE TABLE trivia_draw_config (
                id SERIAL PRIMARY KEY,
                is_custom BOOLEAN NOT NULL DEFAULT FALSE,
                custom_winner_count INTEGER,
                custom_data VARCHAR,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
            """)
            
            # Create trigger function to update updated_at column automatically
            cursor.execute("""
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """)
            
            # Create trigger on the trivia_draw_config table
            cursor.execute("""
            DROP TRIGGER IF EXISTS update_trivia_draw_config_updated_at ON trivia_draw_config;
            """)
            cursor.execute("""
            CREATE TRIGGER update_trivia_draw_config_updated_at
            BEFORE UPDATE ON trivia_draw_config
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
            """)
            
            # Insert default configuration
            # Store draw time settings in custom_data
            default_custom_data = json.dumps({
                "draw_time_hour": int(os.environ.get("DRAW_TIME_HOUR", "20")),
                "draw_time_minute": int(os.environ.get("DRAW_TIME_MINUTE", "0")),
                "draw_timezone": os.environ.get("DRAW_TIMEZONE", "US/Eastern")
            })
            
            cursor.execute("""
            INSERT INTO trivia_draw_config (is_custom, custom_winner_count, custom_data)
            VALUES (FALSE, NULL, %s)
            ON CONFLICT DO NOTHING;
            """, (default_custom_data,))
            
            print("Successfully created trivia_draw_config table with default configuration.")
        else:
            # Check if custom_data column exists
            cursor.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'trivia_draw_config' AND column_name = 'custom_data'
            );
            """)
            
            column_exists = cursor.fetchone()[0]
            
            if not column_exists:
                print("Adding custom_data column to trivia_draw_config table...")
                cursor.execute("""
                ALTER TABLE trivia_draw_config ADD COLUMN custom_data VARCHAR;
                """)
                
                # Update existing records with default custom_data
                default_custom_data = json.dumps({
                    "draw_time_hour": int(os.environ.get("DRAW_TIME_HOUR", "20")),
                    "draw_time_minute": int(os.environ.get("DRAW_TIME_MINUTE", "0")),
                    "draw_timezone": os.environ.get("DRAW_TIMEZONE", "US/Eastern")
                })
                
                cursor.execute("""
                UPDATE trivia_draw_config SET custom_data = %s;
                """, (default_custom_data,))
                
                print("Successfully added custom_data column to trivia_draw_config table.")
            else:
                print("custom_data column already exists in trivia_draw_config table.")
        
        print("trivia_draw_config table update completed successfully!")

    except Exception as e:
        print(f"Error updating trivia_draw_config table: {e}")
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    update_draw_config_table() 