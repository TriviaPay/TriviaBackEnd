import sys
import os
import logging
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_letters_table():
    try:
        connection = engine.connect()
        trans = connection.begin()

        logger.info("Creating letters table...")

        # Create letters table
        connection.execute(text("""
            CREATE TABLE IF NOT EXISTS letters (
                letter VARCHAR PRIMARY KEY,
                image_url VARCHAR NOT NULL
            );
        """))

        # Insert default letter images
        letters = [chr(i) for i in range(ord('a'), ord('z')+1)]
        for letter in letters:
            connection.execute(text(
                "INSERT INTO letters (letter, image_url) VALUES (:letter, :url) ON CONFLICT DO NOTHING"
            ), {
                "letter": letter,
                "url": f"https://example.com/{letter}.png"
            })

        trans.commit()
        logger.info("Letters table created and populated successfully")

    except Exception as e:
        logger.error(f"Error creating letters table: {str(e)}")
        if trans:
            trans.rollback()
        raise
    finally:
        connection.close()

if __name__ == "__main__":
    add_letters_table()
