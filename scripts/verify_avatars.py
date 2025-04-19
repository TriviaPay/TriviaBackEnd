"""
Script to verify avatar selections in the database and fix inconsistencies.
Run with: python scripts/verify_avatars.py
"""

import os
import sys
from datetime import datetime

# Add parent directory to path to allow imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from models import User, Avatar, UserAvatar
from db import get_db
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('avatar_verification.log')
    ]
)

logger = logging.getLogger(__name__)

def verify_avatar_selections():
    """
    Verify all users' avatar selections and report/fix inconsistencies
    """
    db = next(get_db())
    
    logger.info("Starting avatar verification process")
    
    # Get all users with a selected avatar
    users_with_avatars = db.query(User).filter(User.selected_avatar_id != None).all()
    logger.info(f"Found {len(users_with_avatars)} users with selected avatars")
    
    # Get list of all available avatar IDs
    all_avatars = {avatar.id: avatar for avatar in db.query(Avatar).all()}
    logger.info(f"Found {len(all_avatars)} avatars in the database")
    
    # Track statistics
    valid_count = 0
    invalid_count = 0
    fixed_count = 0
    
    for user in users_with_avatars:
        # Check if selected avatar exists
        if user.selected_avatar_id not in all_avatars:
            logger.warning(f"User {user.account_id} has invalid avatar ID: {user.selected_avatar_id}")
            invalid_count += 1
            
            # Try to find a valid owned avatar to set instead
            owned_avatars = db.query(UserAvatar).filter(UserAvatar.user_id == user.account_id).all()
            if owned_avatars:
                new_avatar_id = owned_avatars[0].avatar_id
                if new_avatar_id in all_avatars:
                    user.selected_avatar_id = new_avatar_id
                    logger.info(f"Fixed user {user.account_id} avatar by setting to owned avatar: {new_avatar_id}")
                    fixed_count += 1
                else:
                    user.selected_avatar_id = None
                    logger.info(f"Reset user {user.account_id} avatar to None as owned avatar {new_avatar_id} is also invalid")
                    fixed_count += 1
            else:
                # User has no owned avatars, reset selection
                user.selected_avatar_id = None
                logger.info(f"Reset user {user.account_id} avatar to None as they have no owned avatars")
                fixed_count += 1
        else:
            # Avatar ID is valid
            valid_count += 1
    
    # Commit any changes
    if fixed_count > 0:
        try:
            db.commit()
            logger.info(f"Successfully committed {fixed_count} avatar fixes")
        except Exception as e:
            db.rollback()
            logger.error(f"Error committing avatar fixes: {str(e)}", exc_info=True)
    
    # Report summary
    logger.info("====== Avatar Verification Summary ======")
    logger.info(f"Total users with selected avatars: {len(users_with_avatars)}")
    logger.info(f"Valid selections: {valid_count}")
    logger.info(f"Invalid selections: {invalid_count}")
    logger.info(f"Fixed selections: {fixed_count}")
    logger.info("========================================")
    
    return {
        "total": len(users_with_avatars),
        "valid": valid_count,
        "invalid": invalid_count,
        "fixed": fixed_count
    }

if __name__ == "__main__":
    verify_avatar_selections() 