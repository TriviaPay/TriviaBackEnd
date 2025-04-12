import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from db import DATABASE_URL

def create_trivia_draw_tables():
    """Create or update the trivia draw tables."""
    engine = create_engine(DATABASE_URL)
    conn = engine.connect()

    try:
        # Start a transaction
        trans = conn.begin()

        # Create the trivia_draw_config table if it doesn't exist
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS trivia_draw_config (
            id SERIAL PRIMARY KEY,
            is_custom BOOLEAN DEFAULT FALSE,
            custom_winner_count INTEGER NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """))

        # Create the trivia_draw_winners table if it doesn't exist
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS trivia_draw_winners (
            id SERIAL PRIMARY KEY,
            account_id BIGINT NOT NULL REFERENCES users(account_id),
            prize_amount FLOAT NOT NULL,
            position INTEGER NOT NULL,
            draw_date DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

        # Drop any existing trigger on the trivia_draw_config table
        conn.execute(text("""
        DROP TRIGGER IF EXISTS update_trivia_draw_config_updated_at ON trivia_draw_config;
        """))

        # Create the trigger
        conn.execute(text("""
        CREATE TRIGGER update_trivia_draw_config_updated_at
        BEFORE UPDATE ON trivia_draw_config
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
        """))

        # Insert default configuration if not exists
        conn.execute(text("""
        INSERT INTO trivia_draw_config (is_custom, custom_winner_count)
        VALUES (FALSE, NULL)
        ON CONFLICT DO NOTHING;
        """))

        # Drop any old tables if they exist
        conn.execute(text("""
        DROP TABLE IF EXISTS weekly_winners;
        """))
        
        conn.execute(text("""
        DROP TABLE IF EXISTS daily_winners;
        """))
        
        conn.execute(text("""
        DROP TABLE IF EXISTS draw_entries;
        """))
        
        conn.execute(text("""
        DROP TABLE IF EXISTS draw_config;
        """))
        
        conn.execute(text("""
        DROP TABLE IF EXISTS winner_config;
        """))

        # Commit the transaction
        trans.commit()
        print("Successfully created trivia draw tables!")

    except Exception as e:
        # Rollback the transaction in case of error
        trans.rollback()
        print(f"Error creating trivia draw tables: {e}")
    finally:
        # Close the connection
        conn.close()

if __name__ == "__main__":
    create_trivia_draw_tables() 