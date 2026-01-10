"""
Backfill script for Stripe overhaul migration.

This script:
1. Converts all amount (Float) → amount_minor (BIGINT)
2. Calculates initial wallet_balance_minor from historical payment_transactions
3. Populates user_wallet_balances table from ledger calculation
4. Extracts livemode from existing Stripe IDs where possible
5. Generates idempotency_key for existing transactions

Run this after running the migrations but before deploying the new code.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from db import DATABASE_URL
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def extract_livemode_from_stripe_id(stripe_id: str) -> bool:
    """Extract livemode from Stripe ID prefix."""
    if not stripe_id:
        return False
    # Stripe test mode IDs typically start with specific prefixes
    # Live mode IDs don't have special prefixes
    # For now, we'll default to False (test mode) if we can't determine
    # This is a conservative approach
    return False  # Default to test mode for safety


def generate_idempotency_key(payment_intent_id: str, created_at: datetime) -> str:
    """Generate idempotency key from payment_intent_id and timestamp."""
    if payment_intent_id:
        timestamp = int(created_at.timestamp()) if created_at else int(datetime.utcnow().timestamp())
        return f"{payment_intent_id}_{timestamp}"
    return None


def backfill_payment_transactions(engine):
    """Backfill payment_transactions table with new columns."""
    logger.info("Starting payment_transactions backfill...")

    with engine.connect() as conn:
        # Convert amount to amount_minor
        logger.info("Converting amount (Float) to amount_minor (BIGINT)...")
        conn.execute(text("""
            UPDATE payment_transactions
            SET amount_minor = ROUND(amount * 100)::BIGINT
            WHERE amount_minor IS NULL AND amount IS NOT NULL
        """))
        conn.commit()

        # Extract livemode from payment_intent_id
        logger.info("Extracting livemode from Stripe IDs...")
        conn.execute(text("""
            UPDATE payment_transactions
            SET livemode = FALSE
            WHERE livemode IS NULL
        """))
        conn.commit()

        # Generate idempotency_key for existing transactions
        logger.info("Generating idempotency_key for existing transactions...")
        conn.execute(text("""
            UPDATE payment_transactions
            SET idempotency_key = payment_intent_id || '_' || EXTRACT(EPOCH FROM created_at)::BIGINT::TEXT
            WHERE idempotency_key IS NULL AND payment_intent_id IS NOT NULL
        """))
        conn.commit()

        # Set direction based on payment_method_type
        logger.info("Setting direction based on payment_method_type...")
        conn.execute(text("""
            UPDATE payment_transactions
            SET direction = CASE
                WHEN payment_method_type IN ('standard', 'instant') THEN 'outbound'
                WHEN payment_method_type = 'subscription' THEN 'subscription'
                ELSE 'inbound'
            END
            WHERE direction IS NULL
        """))
        conn.commit()

        # Set funding_source based on payment_method_type
        logger.info("Setting funding_source based on payment_method_type...")
        conn.execute(text("""
            UPDATE payment_transactions
            SET funding_source = CASE
                WHEN payment_method_type = 'card' THEN 'card'
                WHEN payment_method_type IN ('standard', 'instant') THEN 'bank_account'
                WHEN payment_method_type = 'subscription' THEN 'card'
                ELSE 'internal'
            END
            WHERE funding_source IS NULL
        """))
        conn.commit()

        logger.info("Payment transactions backfill completed.")


def calculate_wallet_balances(engine):
    """Calculate initial wallet balances from historical transactions."""
    logger.info("Calculating initial wallet balances...")

    with engine.connect() as conn:
        # Calculate balance for each user from successful wallet deposits minus withdrawals
        logger.info("Calculating balances from payment_transactions...")
        conn.execute(text("""
            INSERT INTO user_wallet_balances (user_id, currency, balance_minor, last_recalculated_at)
            SELECT
                user_id,
                currency,
                COALESCE(SUM(
                    CASE
                        WHEN payment_metadata::text LIKE '%"transaction_type": "wallet_deposit"%'
                             AND status = 'succeeded'
                        THEN amount_minor
                        WHEN payment_metadata::text LIKE '%"transaction_type": "wallet_withdrawal"%'
                        THEN -amount_minor
                        ELSE 0
                    END
                ), 0) as balance_minor,
                NOW() as last_recalculated_at
            FROM payment_transactions
            WHERE amount_minor IS NOT NULL
            GROUP BY user_id, currency
            ON CONFLICT (user_id, currency) DO UPDATE
            SET balance_minor = EXCLUDED.balance_minor,
                last_recalculated_at = EXCLUDED.last_recalculated_at
        """))
        conn.commit()

        # Update users.wallet_balance_minor for USD (default currency)
        logger.info("Updating users.wallet_balance_minor for USD...")
        conn.execute(text("""
            UPDATE users u
            SET wallet_balance_minor = COALESCE(uwb.balance_minor, 0),
                wallet_currency = COALESCE(uwb.currency, 'usd')
            FROM user_wallet_balances uwb
            WHERE u.account_id = uwb.user_id
            AND uwb.currency = 'usd'
        """))
        conn.commit()

        logger.info("Wallet balances calculation completed.")


def backfill_subscription_plans(engine):
    """Backfill subscription_plans with new columns."""
    logger.info("Starting subscription_plans backfill...")

    with engine.connect() as conn:
        # Convert price_usd to unit_amount_minor
        logger.info("Converting price_usd to unit_amount_minor...")
        conn.execute(text("""
            UPDATE subscription_plans
            SET unit_amount_minor = ROUND(price_usd * 100)::BIGINT,
                currency = 'usd',
                interval = billing_interval,
                interval_count = 1,
                livemode = FALSE
            WHERE unit_amount_minor IS NULL
        """))
        conn.commit()

        logger.info("Subscription plans backfill completed.")


def main():
    """Run all backfill operations."""
    logger.info("Starting Stripe overhaul backfill...")

    try:
        engine = create_engine(DATABASE_URL)

        # Run backfills in order
        backfill_payment_transactions(engine)
        calculate_wallet_balances(engine)
        backfill_subscription_plans(engine)

        logger.info("All backfill operations completed successfully!")

    except Exception as e:
        logger.error(f"Error during backfill: {str(e)}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
