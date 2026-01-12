import sys
import os
import logging
from sqlalchemy import text, Column, Integer, String, Float, Boolean, DateTime, BigInteger, Date, ForeignKey
from sqlalchemy.sql import func

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db import engine
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_unified_draw_system():
    try:
        connection = engine.connect()
        trans = connection.begin()

        logger.info("Adding unified draw system components...")

        # 1. Add daily_eligibility_flag column to users table if it doesn't exist
        check_column_query = text("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name='users' AND column_name='daily_eligibility_flag'
        """)
        result = connection.execute(check_column_query)
        if not result.fetchone():
            logger.info("Adding daily_eligibility_flag column to users table")
            connection.execute(text("""
                ALTER TABLE users ADD COLUMN daily_eligibility_flag BOOLEAN DEFAULT FALSE
            """))
        else:
            logger.info("daily_eligibility_flag column already exists")

        # 2. Create user_question_answers table if it doesn't exist
        check_table_query = text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_name='user_question_answers'
        """)
        result = connection.execute(check_table_query)
        if not result.fetchone():
            logger.info("Creating user_question_answers table")
            connection.execute(text("""
                CREATE TABLE user_question_answers (
                    id SERIAL PRIMARY KEY,
                    account_id BIGINT NOT NULL REFERENCES users(account_id),
                    question_number INTEGER NOT NULL,
                    selected_answer VARCHAR NOT NULL,
                    is_correct BOOLEAN NOT NULL,
                    answered_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                    date DATE NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE INDEX ix_user_question_answers_id ON user_question_answers (id)
            """))
            connection.execute(text("""
                CREATE INDEX ix_user_question_answers_account_id ON user_question_answers (account_id)
            """))
            connection.execute(text("""
                CREATE INDEX ix_user_question_answers_date ON user_question_answers (date)
            """))
        else:
            logger.info("user_question_answers table already exists")

        # 3. Create company_revenue table if it doesn't exist
        check_table_query = text("""
            SELECT 1 FROM information_schema.tables
            WHERE table_name='company_revenue'
        """)
        result = connection.execute(check_table_query)
        if not result.fetchone():
            logger.info("Creating company_revenue table")
            connection.execute(text("""
                CREATE TABLE company_revenue (
                    id SERIAL PRIMARY KEY,
                    month_start_date DATE NOT NULL UNIQUE,
                    revenue_amount FLOAT NOT NULL,
                    subscriber_count INTEGER NOT NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
                )
            """))
            connection.execute(text("""
                CREATE INDEX ix_company_revenue_id ON company_revenue (id)
            """))
            connection.execute(text("""
                CREATE INDEX ix_company_revenue_month ON company_revenue (month_start_date)
            """))
        else:
            logger.info("company_revenue table already exists")

        trans.commit()
        connection.close()
        logger.info("Unified draw system migration completed successfully")
        return True

    except Exception as e:
        logger.error(f"Error in unified draw system migration: {str(e)}")
        if 'trans' in locals():
            trans.rollback()
        if 'connection' in locals():
            connection.close()
        return False

if __name__ == "__main__":
    logger.info("Starting unified draw system migration")
    success = add_unified_draw_system()
    if success:
        logger.info("Migration completed successfully")
        print("Migration completed successfully")
    else:
        logger.error("Migration failed")
        print("Migration failed")
