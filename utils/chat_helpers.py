"""
Helper functions for chat-related operations.
"""
from typing import Dict, Optional
from sqlalchemy.orm import Session
import logging

from models import User, Avatar, Frame, Badge
from utils.storage import presign_get

logger = logging.getLogger(__name__)


def get_user_chat_profile_data(user: User, db: Session) -> Dict:
    """
    Get user profile data for chat responses including profile pic, avatar, frame, and badge.
    
    Args:
        user: User object
        db: Database session
        
    Returns:
        Dictionary with:
        - profile_pic_url: str or None (custom uploaded profile picture)
        - avatar_url: str or None (presigned S3 URL for selected avatar)
        - frame_url: str or None (presigned S3 URL for selected frame)
        - badge: dict or None with id, name, image_url (public URL, no presigning needed)
    """
    profile_pic_url = user.profile_pic_url
    
    # Get avatar URL (presigned)
    avatar_url = None
    if user.selected_avatar_id:
        avatar_obj = db.query(Avatar).filter(Avatar.id == user.selected_avatar_id).first()
        if avatar_obj:
            bucket = getattr(avatar_obj, "bucket", None)
            object_key = getattr(avatar_obj, "object_key", None)
            if bucket and object_key:
                try:
                    avatar_url = presign_get(bucket, object_key, expires=900)
                except Exception as e:
                    logger.warning(f"Failed to presign avatar {avatar_obj.id} for user {user.account_id}: {e}")
            else:
                logger.debug(f"Avatar {avatar_obj.id} missing bucket/object_key for user {user.account_id}")
    
    # Get frame URL (presigned)
    frame_url = None
    if user.selected_frame_id:
        frame_obj = db.query(Frame).filter(Frame.id == user.selected_frame_id).first()
        if frame_obj:
            bucket = getattr(frame_obj, "bucket", None)
            object_key = getattr(frame_obj, "object_key", None)
            if bucket and object_key:
                try:
                    frame_url = presign_get(bucket, object_key, expires=900)
                except Exception as e:
                    logger.warning(f"Failed to presign frame {frame_obj.id} for user {user.account_id}: {e}")
            else:
                logger.debug(f"Frame {frame_obj.id} missing bucket/object_key for user {user.account_id}")
    
    # Get badge information
    badge_info = None
    if user.badge_id:
        badge = db.query(Badge).filter(Badge.id == user.badge_id).first()
        if badge:
            badge_info = {
                "id": badge.id,
                "name": badge.name,
                "image_url": badge.image_url  # Public URL, no presigning needed
            }
    
    return {
        "profile_pic_url": profile_pic_url,
        "avatar_url": avatar_url,
        "frame_url": frame_url,
        "badge": badge_info
    }

