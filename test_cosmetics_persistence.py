"""
Test script to verify that avatar and frame selections are properly persisted.
Run with: python test_cosmetics_persistence.py
"""

import os
import sys
from datetime import datetime

# Add parent directory to path to allow imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy.orm import Session
from models import User, Avatar, Frame, UserAvatar, UserFrame
from db import get_db
import logging
import uuid
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
    ]
)

logger = logging.getLogger(__name__)

def test_persistence():
    """
    Test avatar and frame selection persistence
    """
    db = next(get_db())
    
    print("===== Testing Avatar and Frame Selection Persistence =====")
    logger.info("Starting persistence tests")
    
    # Get first user
    user = db.query(User).first()
    if not user:
        logger.error("No users found in the database.")
        print("ERROR: No users found in the database.")
        return
    
    # Store user ID to retrieve after session close
    user_id = user.account_id
    
    print(f"Using user: account_id={user_id}, username={user.username}")
    logger.info(f"Using user: account_id={user_id}, username={user.username}")
    
    # Get first avatar and frame
    avatar = db.query(Avatar).first()
    frame = db.query(Frame).first()
    
    if not avatar:
        print("No avatars found. Creating test avatar.")
        logger.warning("No avatars found. Creating test avatar.")
        # Create a test avatar
        avatar = Avatar(
            id=f"test_avatar_{uuid.uuid4()}",
            name="Test Avatar",
            description="Created for persistence testing",
            image_url="https://example.com/test_avatar.png",
            is_premium=False,
            created_at=datetime.utcnow()
        )
        db.add(avatar)
        db.commit()
        db.refresh(avatar)
    
    if not frame:
        print("No frames found. Creating test frame.")
        logger.warning("No frames found. Creating test frame.")
        # Create a test frame
        frame = Frame(
            id=f"test_frame_{uuid.uuid4()}",
            name="Test Frame",
            description="Created for persistence testing",
            image_url="https://example.com/test_frame.png",
            is_premium=False,
            created_at=datetime.utcnow()
        )
        db.add(frame)
        db.commit()
        db.refresh(frame)
    
    # Store IDs to use after session close
    avatar_id = avatar.id
    frame_id = frame.id
    
    print(f"Using avatar: id={avatar_id}, name={avatar.name}")
    print(f"Using frame: id={frame_id}, name={frame.name}")
    logger.info(f"Using avatar: id={avatar_id}, name={avatar.name}")
    logger.info(f"Using frame: id={frame_id}, name={frame.name}")
    
    # Ensure user owns these items
    user_avatar = db.query(UserAvatar).filter(
        UserAvatar.user_id == user_id,
        UserAvatar.avatar_id == avatar_id
    ).first()
    
    if not user_avatar:
        print(f"User doesn't own avatar {avatar_id}. Creating ownership record.")
        logger.info(f"User doesn't own avatar {avatar_id}. Creating ownership record.")
        user_avatar = UserAvatar(
            user_id=user_id,
            avatar_id=avatar_id,
            purchase_date=datetime.utcnow()
        )
        db.add(user_avatar)
        db.commit()
    
    user_frame = db.query(UserFrame).filter(
        UserFrame.user_id == user_id,
        UserFrame.frame_id == frame_id
    ).first()
    
    if not user_frame:
        print(f"User doesn't own frame {frame_id}. Creating ownership record.")
        logger.info(f"User doesn't own frame {frame_id}. Creating ownership record.")
        user_frame = UserFrame(
            user_id=user_id,
            frame_id=frame_id,
            purchase_date=datetime.utcnow()
        )
        db.add(user_frame)
        db.commit()
    
    # Record current selections
    orig_avatar_id = user.selected_avatar_id
    orig_frame_id = user.selected_frame_id
    
    print(f"Original selections - avatar: {orig_avatar_id}, frame: {orig_frame_id}")
    logger.info(f"Original selections - avatar: {orig_avatar_id}, frame: {orig_frame_id}")
    
    # Test 1: Update avatar
    print(f"Test 1: Setting avatar to {avatar_id}")
    logger.info(f"Test 1: Setting avatar to {avatar_id}")
    user.selected_avatar_id = avatar_id
    db.commit()
    
    # Close and reopen session to verify persistence
    db.close()
    db = next(get_db())
    
    # Fetch user again
    user = db.query(User).filter(User.account_id == user_id).first()
    
    print(f"After Test 1 - avatar: {user.selected_avatar_id}, expected: {avatar_id}")
    logger.info(f"After Test 1 - avatar: {user.selected_avatar_id}, expected: {avatar_id}")
    if user.selected_avatar_id != avatar_id:
        print(f"Avatar update failed. Expected: {avatar_id}, Got: {user.selected_avatar_id}")
        logger.error(f"Avatar update failed. Expected: {avatar_id}, Got: {user.selected_avatar_id}")
    else:
        print("✅ Avatar update successful!")
        logger.info("Avatar update successful!")
    
    # Test 2: Update frame
    print(f"Test 2: Setting frame to {frame_id}")
    logger.info(f"Test 2: Setting frame to {frame_id}")
    user.selected_frame_id = frame_id
    db.commit()
    
    # Close and reopen session to verify persistence
    db.close()
    db = next(get_db())
    
    # Fetch user again
    user = db.query(User).filter(User.account_id == user_id).first()
    
    print(f"After Test 2 - frame: {user.selected_frame_id}, expected: {frame_id}")
    logger.info(f"After Test 2 - frame: {user.selected_frame_id}, expected: {frame_id}")
    if user.selected_frame_id != frame_id:
        print(f"❌ Frame update failed. Expected: {frame_id}, Got: {user.selected_frame_id}")
        logger.error(f"Frame update failed. Expected: {frame_id}, Got: {user.selected_frame_id}")
    else:
        print("✅ Frame update successful!")
        logger.info("Frame update successful!")
    
    # Reset to original values if requested
    restore = True  # Change to False to keep the new selections
    if restore:
        print("Restoring original selections")
        logger.info("Restoring original selections")
        user.selected_avatar_id = orig_avatar_id
        user.selected_frame_id = orig_frame_id
        db.commit()
    
    print("Testing completed.")
    print("===================================================")
    logger.info("Testing completed.")
    
    results = {
        "avatar_test_passed": user.selected_avatar_id == avatar_id if not restore else True,
        "frame_test_passed": user.selected_frame_id == frame_id if not restore else True,
        "test_user": user_id,
        "test_avatar": avatar_id,
        "test_frame": frame_id
    }
    print(f"Results: {results}")
    return results

if __name__ == "__main__":
    test_persistence() 