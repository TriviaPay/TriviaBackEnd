import sys
import os
import logging
from datetime import datetime

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import Column, Integer, String, Boolean, Float, DateTime, ForeignKey, text
from sqlalchemy.ext.declarative import declarative_base
from db import engine, get_db
from sqlalchemy.exc import SQLAlchemyError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_bank_accounts_table():
    """Create user_bank_accounts table for storing bank details"""
    logger.info("Starting migration to create bank accounts table...")
    
    with engine.connect() as conn:
        try:
            # Check if the table already exists
            result = conn.execute(text("SELECT to_regclass('public.user_bank_accounts')"))
            table_exists = result.scalar() is not None
            
            if not table_exists:
                conn.execute(text("""
                    CREATE TABLE user_bank_accounts (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL REFERENCES users(account_id),
                        account_name VARCHAR NOT NULL,
                        account_number_last4 VARCHAR(4) NOT NULL,
                        account_number_encrypted VARCHAR,
                        routing_number_encrypted VARCHAR,
                        bank_name VARCHAR NOT NULL,
                        is_default BOOLEAN DEFAULT FALSE,
                        is_verified BOOLEAN DEFAULT FALSE,
                        stripe_bank_account_id VARCHAR,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    );
                    CREATE INDEX idx_user_bank_accounts_user_id ON user_bank_accounts(user_id);
                    CREATE INDEX idx_user_bank_accounts_is_default ON user_bank_accounts(is_default);
                """))
                logger.info("Created user_bank_accounts table successfully")
            else:
                logger.info("user_bank_accounts table already exists, skipping creation")
                
            # Check if subscription_plans table exists
            result = conn.execute(text("SELECT to_regclass('public.subscription_plans')"))
            table_exists = result.scalar() is not None
            
            if not table_exists:
                conn.execute(text("""
                    CREATE TABLE subscription_plans (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR NOT NULL,
                        description VARCHAR,
                        price_usd FLOAT NOT NULL,
                        billing_interval VARCHAR NOT NULL,
                        features VARCHAR,
                        stripe_price_id VARCHAR,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    );
                """))
                logger.info("Created subscription_plans table successfully")
            else:
                logger.info("subscription_plans table already exists, skipping creation")
                
            # Check if user_subscriptions table exists
            result = conn.execute(text("SELECT to_regclass('public.user_subscriptions')"))
            table_exists = result.scalar() is not None
            
            if not table_exists:
                conn.execute(text("""
                    CREATE TABLE user_subscriptions (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT NOT NULL REFERENCES users(account_id),
                        plan_id INTEGER NOT NULL REFERENCES subscription_plans(id),
                        stripe_subscription_id VARCHAR,
                        status VARCHAR NOT NULL,
                        current_period_start TIMESTAMP,
                        current_period_end TIMESTAMP,
                        cancel_at_period_end BOOLEAN DEFAULT FALSE,
                        payment_method_id VARCHAR,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    );
                    CREATE INDEX idx_user_subscriptions_user_id ON user_subscriptions(user_id);
                    CREATE INDEX idx_user_subscriptions_status ON user_subscriptions(status);
                """))
                logger.info("Created user_subscriptions table successfully")
            else:
                logger.info("user_subscriptions table already exists, skipping creation")
                
            # Create default subscription plans
            conn.execute(text("""
                INSERT INTO subscription_plans (name, description, price_usd, billing_interval, features)
                SELECT 'Basic Plan', 'Monthly subscription with basic features', 9.99, 'month', '{"feature1": "Basic access", "feature2": "Standard support"}'
                WHERE NOT EXISTS (SELECT 1 FROM subscription_plans WHERE name = 'Basic Plan');
                
                INSERT INTO subscription_plans (name, description, price_usd, billing_interval, features)
                SELECT 'Premium Plan', 'Monthly subscription with premium features', 19.99, 'month', '{"feature1": "Premium access", "feature2": "Priority support", "feature3": "Advanced features"}'
                WHERE NOT EXISTS (SELECT 1 FROM subscription_plans WHERE name = 'Premium Plan');
                
                INSERT INTO subscription_plans (name, description, price_usd, billing_interval, features)
                SELECT 'Annual Plan', 'Annual subscription with all features', 99.99, 'year', '{"feature1": "Full access", "feature2": "Premium support", "feature3": "All advanced features", "feature4": "2 months free"}'
                WHERE NOT EXISTS (SELECT 1 FROM subscription_plans WHERE name = 'Annual Plan');
            """))
            
            conn.commit()
            logger.info("Bank accounts and subscription tables migration completed successfully!")
            
        except SQLAlchemyError as e:
            logger.error(f"Database error during migration: {e}")
            conn.rollback()
            raise
        except Exception as e:
            logger.error(f"Error creating tables: {e}")
            conn.rollback()
            raise

if __name__ == "__main__":
    create_bank_accounts_table() 