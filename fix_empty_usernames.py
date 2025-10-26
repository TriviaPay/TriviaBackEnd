#!/usr/bin/env python3
"""
Script to fix users with empty or null usernames.
This ensures all users have proper usernames for live chat display.
"""

import sys
import os
import logging
from sqlalchemy.orm import Session

# Add the project root to the Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import SessionLocal
from models import User

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_empty_usernames():
    """
    Fix users with empty or null usernames by generating usernames from email.
    """
    db: Session = SessionLocal()
    try:
        # Find users with empty or null usernames
        users_with_empty_usernames = db.query(User).filter(
            (User.username == None) | 
            (User.username == '') | 
            (User.username == 'null')
        ).all()
        
        logger.info(f"Found {len(users_with_empty_usernames)} users with empty usernames")
        
        fixed_count = 0
        for user in users_with_empty_usernames:
            try:
                # Generate username from email
                if user.email:
                    email_prefix = user.email.split('@')[0]
                    # Clean the email prefix (remove special characters, limit length)
                    clean_username = ''.join(c for c in email_prefix if c.isalnum() or c in '._-')[:20]
                    
                    # Check if this username is already taken
                    existing_user = db.query(User).filter(
                        User.username == clean_username,
                        User.account_id != user.account_id
                    ).first()
                    
                    if existing_user:
                        # If taken, append account_id
                        clean_username = f"{clean_username}{user.account_id}"
                    
                    user.username = clean_username
                    logger.info(f"Fixed username for user {user.account_id}: '{clean_username}' (from email: {user.email})")
                    fixed_count += 1
                else:
                    # No email either, use account_id
                    user.username = f"User{user.account_id}"
                    logger.info(f"Fixed username for user {user.account_id}: 'User{user.account_id}' (no email)")
                    fixed_count += 1
                    
            except Exception as e:
                logger.error(f"Error fixing username for user {user.account_id}: {str(e)}")
                continue
        
        # Commit all changes
        db.commit()
        logger.info(f"Successfully fixed {fixed_count} usernames")
        
        # Verify the fix
        remaining_empty = db.query(User).filter(
            (User.username == None) | 
            (User.username == '') | 
            (User.username == 'null')
        ).count()
        
        logger.info(f"Remaining users with empty usernames: {remaining_empty}")
        
        return fixed_count
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error fixing usernames: {str(e)}")
        raise
    finally:
        db.close()

def verify_usernames():
    """
    Verify that all users have proper usernames.
    """
    db: Session = SessionLocal()
    try:
        total_users = db.query(User).count()
        users_with_usernames = db.query(User).filter(
            User.username != None,
            User.username != '',
            User.username != 'null'
        ).count()
        
        logger.info(f"Total users: {total_users}")
        logger.info(f"Users with usernames: {users_with_usernames}")
        logger.info(f"Users without usernames: {total_users - users_with_usernames}")
        
        return total_users - users_with_usernames == 0
        
    except Exception as e:
        logger.error(f"Error verifying usernames: {str(e)}")
        return False
    finally:
        db.close()

if __name__ == "__main__":
    logger.info("Starting username fix process...")
    
    # First verify current state
    logger.info("Verifying current username state...")
    verify_usernames()
    
    # Fix empty usernames
    logger.info("Fixing empty usernames...")
    fixed_count = fix_empty_usernames()
    
    # Verify the fix
    logger.info("Verifying fix...")
    is_fixed = verify_usernames()
    
    if is_fixed:
        logger.info("✅ All usernames are now properly set!")
    else:
        logger.warning("⚠️ Some usernames may still be empty")
    
    logger.info("Username fix process completed.")
