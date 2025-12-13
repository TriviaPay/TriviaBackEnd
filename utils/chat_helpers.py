"""
Helper functions for chat-related operations.
"""
from typing import Dict, Optional, List, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from datetime import datetime
import logging

from models import User, Avatar, Frame, Badge, UserSubscription, SubscriptionPlan
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
    
    # Get badge information (achievement badge)
    badge_info = None
    if user.badge_id:
        badge = db.query(Badge).filter(Badge.id == user.badge_id).first()
        if badge:
            badge_info = {
                "id": badge.id,
                "name": badge.name,
                "image_url": badge.image_url  # Public URL, no presigning needed
            }
    
    # Get subscription badges
    subscription_badges = []
    
    # Check for active bronze ($5) subscription
    bronze_subscription = db.query(UserSubscription).join(SubscriptionPlan).filter(
        and_(
            UserSubscription.user_id == user.account_id,
            UserSubscription.status == 'active',
            or_(
                SubscriptionPlan.unit_amount_minor == 500,  # $5.00 in cents
                SubscriptionPlan.price_usd == 5.0
            ),
            UserSubscription.current_period_end > datetime.utcnow()
        )
    ).first()
    
    if bronze_subscription:
        # Get bronze badge - try multiple possible badge ID patterns or match by name
        bronze_badge = None
        # First try exact matches
        for badge_id in ['bronze', 'bronze_badge', 'brone_badge', 'brone']:
            bronze_badge = db.query(Badge).filter(Badge.id == badge_id).first()
            if bronze_badge:
                break
        # If not found, try case-insensitive name match
        if not bronze_badge:
            bronze_badge = db.query(Badge).filter(Badge.name.ilike('%bronze%')).first()
        
        if bronze_badge:
            subscription_badges.append({
                "id": bronze_badge.id,
                "name": bronze_badge.name,
                "image_url": bronze_badge.image_url,
                "subscription_type": "bronze",
                "price": 5.0
            })
    
    # Check for active silver ($10) subscription
    silver_subscription = db.query(UserSubscription).join(SubscriptionPlan).filter(
        and_(
            UserSubscription.user_id == user.account_id,
            UserSubscription.status == 'active',
            or_(
                SubscriptionPlan.unit_amount_minor == 1000,  # $10.00 in cents
                SubscriptionPlan.price_usd == 10.0
            ),
            UserSubscription.current_period_end > datetime.utcnow()
        )
    ).first()
    
    if silver_subscription:
        # Get silver badge - try multiple possible badge ID patterns or match by name
        silver_badge = None
        # First try exact matches
        for badge_id in ['silver', 'silver_badge']:
            silver_badge = db.query(Badge).filter(Badge.id == badge_id).first()
            if silver_badge:
                break
        # If not found, try case-insensitive name match
        if not silver_badge:
            silver_badge = db.query(Badge).filter(Badge.name.ilike('%silver%')).first()
        
        if silver_badge:
            subscription_badges.append({
                "id": silver_badge.id,
                "name": silver_badge.name,
                "image_url": silver_badge.image_url,
                "subscription_type": "silver",
                "price": 10.0
            })
    
    return {
        "profile_pic_url": profile_pic_url,
        "avatar_url": avatar_url,
        "frame_url": frame_url,
        "badge": badge_info,  # Achievement badge
        "subscription_badges": subscription_badges  # Array of subscription badge URLs
    }

