"""
Script to verify frame selections in the database and fix inconsistencies.
Run with: python scripts/verify_frames.py
"""

import os
import sys
from datetime import datetime

# Add parent directory to path to allow imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from models import User, Frame, UserFrame
from db import get_db
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('frame_verification.log')
    ]
)

logger = logging.getLogger(__name__)

def verify_frame_selections():
    """
    Verify all users' frame selections and report/fix inconsistencies
    """
    db = next(get_db())
    
    logger.info("Starting frame verification process")
    
    # Get all users with a selected frame
    users_with_frames = db.query(User).filter(User.selected_frame_id != None).all()
    logger.info(f"Found {len(users_with_frames)} users with selected frames")
    
    # Get list of all available frame IDs
    all_frames = {frame.id: frame for frame in db.query(Frame).all()}
    logger.info(f"Found {len(all_frames)} frames in the database")
    
    # Track statistics
    valid_count = 0
    invalid_count = 0
    fixed_count = 0
    
    for user in users_with_frames:
        # Check if selected frame exists
        if user.selected_frame_id not in all_frames:
            logger.warning(f"User {user.account_id} has invalid frame ID: {user.selected_frame_id}")
            invalid_count += 1
            
            # Try to find a valid owned frame to set instead
            owned_frames = db.query(UserFrame).filter(UserFrame.user_id == user.account_id).all()
            if owned_frames:
                new_frame_id = owned_frames[0].frame_id
                if new_frame_id in all_frames:
                    user.selected_frame_id = new_frame_id
                    logger.info(f"Fixed user {user.account_id} frame by setting to owned frame: {new_frame_id}")
                    fixed_count += 1
                else:
                    user.selected_frame_id = None
                    logger.info(f"Reset user {user.account_id} frame to None as owned frame {new_frame_id} is also invalid")
                    fixed_count += 1
            else:
                # User has no owned frames, reset selection
                user.selected_frame_id = None
                logger.info(f"Reset user {user.account_id} frame to None as they have no owned frames")
                fixed_count += 1
        else:
            # Frame ID is valid
            valid_count += 1
    
    # Commit any changes
    if fixed_count > 0:
        try:
            db.commit()
            logger.info(f"Successfully committed {fixed_count} frame fixes")
        except Exception as e:
            db.rollback()
            logger.error(f"Error committing frame fixes: {str(e)}", exc_info=True)
    
    # Report summary
    logger.info("====== Frame Verification Summary ======")
    logger.info(f"Total users with selected frames: {len(users_with_frames)}")
    logger.info(f"Valid selections: {valid_count}")
    logger.info(f"Invalid selections: {invalid_count}")
    logger.info(f"Fixed selections: {fixed_count}")
    logger.info("=======================================")
    
    return {
        "total": len(users_with_frames),
        "valid": valid_count,
        "invalid": invalid_count,
        "fixed": fixed_count
    }

if __name__ == "__main__":
    verify_frame_selections() 