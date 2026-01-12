import logging
from datetime import datetime

from sqlalchemy import text

from core.db import SessionLocal
from models import GemPackageConfig, User, UserGemPurchase

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def fix_one_time_purchases():
    """
    Retroactively add records for one-time purchases that have already been made.
    This is a one-time script to fix existing data after introducing the UserGemPurchase model.
    """
    db = SessionLocal()
    try:
        # Get all one-time offer packages
        one_time_packages = (
            db.query(GemPackageConfig)
            .filter(GemPackageConfig.is_one_time == True)
            .all()
        )
        if not one_time_packages:
            logger.info("No one-time packages found.")
            return True

        logger.info(f"Found {len(one_time_packages)} one-time packages to process.")

        # Get users who have made purchases
        users = (
            db.query(User).filter(User.wallet_balance < 1000).all()
        )  # Assuming users with wallet_balance < 1000 have made purchases
        logger.info(f"Found {len(users)} users to check.")

        # For each one-time package and user, create a record if not exists
        created_count = 0
        for package in one_time_packages:
            logger.info(
                f"Processing package ID {package.id}: {package.gems_amount} gems for ${package.price_usd}"
            )

            # For each user, create a record for this package
            for user in users:
                # Check if a record already exists
                existing = (
                    db.query(UserGemPurchase)
                    .filter(
                        UserGemPurchase.user_id == user.account_id,
                        UserGemPurchase.package_id == package.id,
                    )
                    .first()
                )

                if not existing:
                    # Assume they've purchased it once
                    purchase = UserGemPurchase(
                        user_id=user.account_id,
                        package_id=package.id,
                        purchase_date=datetime.utcnow(),
                        price_paid=package.price_usd,
                        gems_received=package.gems_amount,
                    )
                    db.add(purchase)
                    created_count += 1

        if created_count > 0:
            db.commit()
            logger.info(
                f"Created {created_count} purchase records for one-time packages."
            )
        else:
            logger.info("No new records needed to be created.")

        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Error fixing one-time purchases: {str(e)}")
        return False
    finally:
        db.close()


if __name__ == "__main__":
    success = fix_one_time_purchases()
    if success:
        print("One-time purchases fixed successfully.")
    else:
        print("Failed to fix one-time purchases.")
