import logging

from sqlalchemy import text

from db import SessionLocal, engine

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_user_gem_purchases_table():
    """
    Create the user_gem_purchases table to track one-time gem package purchases
    """
    db = SessionLocal()
    try:
        # Check if table already exists
        logger.info("Checking if user_gem_purchases table exists...")
        result = db.execute(
            text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'user_gem_purchases')"
            )
        )
        table_exists = result.scalar()

        if not table_exists:
            # Create the table
            logger.info("Creating user_gem_purchases table...")
            db.execute(
                text(
                    """
            CREATE TABLE user_gem_purchases (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(account_id),
                package_id INTEGER NOT NULL REFERENCES gem_package_config(id),
                purchase_date TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL,
                price_paid FLOAT NOT NULL,
                gems_received INTEGER NOT NULL
            );
            CREATE INDEX ix_user_gem_purchases_id ON user_gem_purchases (id);
            """
                )
            )
            db.commit()
            logger.info("Table created successfully!")
        else:
            logger.info("Table already exists, skipping creation.")

        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating table: {str(e)}")
        return False
    finally:
        db.close()


if __name__ == "__main__":
    success = create_user_gem_purchases_table()
    if success:
        print("User gem purchases table created or already exists.")
    else:
        print("Failed to create user gem purchases table.")
