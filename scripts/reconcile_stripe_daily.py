#!/usr/bin/env python3
"""
Daily Stripe Reconciliation Script

Run this script daily via cron to reconcile Stripe balances with platform ledger.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from db import DATABASE_URL
from utils.reconciliation import reconcile_all_currencies
import logging
from datetime import date

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Run daily reconciliation."""
    logger.info("Starting daily Stripe reconciliation...")
    
    try:
        engine = create_engine(DATABASE_URL)
        Session = sessionmaker(bind=engine)
        db = Session()
        
        # Reconcile all supported currencies
        currencies = ['usd']  # Add more currencies as needed
        results = reconcile_all_currencies(db, currencies)
        
        # Log results
        for result in results:
            if 'error' in result:
                logger.error(f"Reconciliation failed for {result.get('currency', 'unknown')}: {result['error']}")
            elif result.get('has_discrepancy'):
                logger.warning(
                    f"Discrepancy detected for {result['currency']}: "
                    f"available_diff={result.get('available_discrepancy_minor', 0)}, "
                    f"pending_diff={result.get('pending_discrepancy_minor', 0)}"
                )
            else:
                logger.info(f"Reconciliation passed for {result['currency']}")
        
        db.close()
        logger.info("Daily reconciliation completed")
        
    except Exception as e:
        logger.error(f"Error during daily reconciliation: {str(e)}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

