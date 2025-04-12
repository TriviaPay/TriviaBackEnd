import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from db import DATABASE_URL

def create_draw_config_table():
    """Create draw_config table."""
    engine = create_engine(DATABASE_URL)
    conn = engine.connect()

    try:
        # Start a transaction
        trans = conn.begin()

        # Create the draw_config table if it doesn't exist
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS draw_config (
            id SERIAL PRIMARY KEY,
            is_custom BOOLEAN DEFAULT FALSE,
            custom_winner_count INTEGER NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """))

        # Create a trigger to update the updated_at column
        conn.execute(text("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = CURRENT_TIMESTAMP;
            RETURN NEW;
        END;
        $$ language 'plpgsql';
        """))

        # Drop any existing trigger on the draw_config table
        conn.execute(text("""
        DROP TRIGGER IF EXISTS update_draw_config_updated_at ON draw_config;
        """))

        # Create the trigger
        conn.execute(text("""
        CREATE TRIGGER update_draw_config_updated_at
        BEFORE UPDATE ON draw_config
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
        """))

        # Insert default configuration if not exists
        conn.execute(text("""
        INSERT INTO draw_config (is_custom, custom_winner_count)
        VALUES (FALSE, NULL)
        ON CONFLICT DO NOTHING;
        """))

        # Commit the transaction
        trans.commit()
        print("Successfully created draw_config table with default configuration!")

    except Exception as e:
        # Rollback the transaction in case of error
        trans.rollback()
        print(f"Error creating draw_config table: {e}")
    finally:
        # Close the connection
        conn.close()

if __name__ == "__main__":
    create_draw_config_table() 