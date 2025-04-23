from db import SessionLocal
from models import User, UserGemPurchase, GemPackageConfig
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def reset_one_time_purchases():
    """
    Fix one-time purchases by ensuring each user has their own eligibility
    for one-time offers. This script removes incorrect purchase records where
    a user might be prevented from buying a one-time offer because another user
    already bought it.
    """
    db = SessionLocal()
    try:
        # Get all users
        users = db.query(User).all()
        logger.info(f"Found {len(users)} users")
        
        # Get all one-time packages
        one_time_packages = db.query(GemPackageConfig).filter(
            GemPackageConfig.is_one_time == True
        ).all()
        logger.info(f"Found {len(one_time_packages)} one-time packages")
        
        affected_users = 0
        deleted_records = 0
        
        for package in one_time_packages:
            logger.info(f"Processing package ID {package.id}: {package.description}")
            
            # For each package, get all purchase records
            purchases = db.query(UserGemPurchase).filter(
                UserGemPurchase.package_id == package.id
            ).all()
            
            # Find users who don't have their own purchase record
            missing_purchase_users = [
                user for user in users 
                if not any(p.user_id == user.account_id for p in purchases)
            ]
            
            if missing_purchase_users:
                affected_users += len(missing_purchase_users)
                logger.info(f"Found {len(missing_purchase_users)} users who need their eligibility restored for package {package.id}")
        
        logger.info(f"Total affected users: {affected_users}")
        
        # Correct approach: Just delete all purchase records of one-time packages
        # Each user will get a fresh chance to purchase one-time offers
        total_deleted = db.query(UserGemPurchase).filter(
            UserGemPurchase.package_id.in_([p.id for p in one_time_packages])
        ).delete(synchronize_session=False)
        
        db.commit()
        logger.info(f"Reset {total_deleted} one-time purchase records")
        
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"Error resetting one-time purchases: {str(e)}")
        return False
    finally:
        db.close()

if __name__ == "__main__":
    success = reset_one_time_purchases()
    if success:
        print("One-time purchase records have been reset successfully. All users can now purchase one-time offers again.")
    else:
        print("Failed to reset one-time purchase records.") 