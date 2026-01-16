"""
Helper functions for chat-related operations.
"""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from models import (
    Avatar,
    Frame,
    SubscriptionPlan,
    TriviaModeConfig,
    User,
    UserSubscription,
)
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
        avatar_obj = (
            db.query(Avatar).filter(Avatar.id == user.selected_avatar_id).first()
        )
        if avatar_obj:
            bucket = getattr(avatar_obj, "bucket", None)
            object_key = getattr(avatar_obj, "object_key", None)
            if bucket and object_key:
                try:
                    avatar_url = presign_get(bucket, object_key, expires=900)
                except Exception as e:
                    logger.warning(
                        f"Failed to presign avatar {avatar_obj.id} for user {user.account_id}: {e}"
                    )
            else:
                logger.debug(
                    f"Avatar {avatar_obj.id} missing bucket/object_key for user {user.account_id}"
                )

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
                    logger.warning(
                        f"Failed to presign frame {frame_obj.id} for user {user.account_id}: {e}"
                    )
            else:
                logger.debug(
                    f"Frame {frame_obj.id} missing bucket/object_key for user {user.account_id}"
                )

    # Get badge information (achievement badge) - now from TriviaModeConfig
    badge_info = None
    if user.badge_id:
        mode_config = (
            db.query(TriviaModeConfig)
            .filter(TriviaModeConfig.mode_id == user.badge_id)
            .first()
        )
        if mode_config and mode_config.badge_image_url:
            badge_info = {
                "id": mode_config.mode_id,
                "name": mode_config.mode_name,
                "image_url": mode_config.badge_image_url,  # Public URL, no presigning needed
            }

    # Get subscription badges
    subscription_badges = []
    active_subscriptions = (
        db.query(SubscriptionPlan.unit_amount_minor, SubscriptionPlan.price_usd)
        .join(UserSubscription)
        .filter(
            and_(
                UserSubscription.user_id == user.account_id,
                UserSubscription.status == "active",
                UserSubscription.current_period_end > datetime.utcnow(),
                or_(
                    SubscriptionPlan.unit_amount_minor.in_([500, 1000]),
                    SubscriptionPlan.price_usd.in_([5.0, 10.0]),
                ),
            )
        )
        .all()
    )

    has_bronze = any(
        unit_amount_minor == 500 or price_usd == 5.0
        for unit_amount_minor, price_usd in active_subscriptions
    )
    has_silver = any(
        unit_amount_minor == 1000 or price_usd == 10.0
        for unit_amount_minor, price_usd in active_subscriptions
    )

    badge_map = {}
    if has_bronze or has_silver:
        badge_candidates = [
            "bronze",
            "bronze_badge",
            "brone_badge",
            "brone",
            "silver",
            "silver_badge",
        ]
        badges = (
            db.query(TriviaModeConfig)
            .filter(
                TriviaModeConfig.mode_id.in_(badge_candidates),
                TriviaModeConfig.badge_image_url.isnot(None),
            )
            .all()
        )
        badge_map = {badge.mode_id: badge for badge in badges}

    bronze_badge = None
    if has_bronze:
        for mode_id in ["bronze", "bronze_badge", "brone_badge", "brone"]:
            bronze_badge = badge_map.get(mode_id)
            if bronze_badge:
                break
        if not bronze_badge:
            bronze_badge = (
                db.query(TriviaModeConfig)
                .filter(
                    TriviaModeConfig.mode_name.ilike("%bronze%"),
                    TriviaModeConfig.badge_image_url.isnot(None),
                )
                .first()
            )

    if bronze_badge and bronze_badge.badge_image_url:
        subscription_badges.append(
            {
                "id": bronze_badge.mode_id,
                "name": bronze_badge.mode_name,
                "image_url": bronze_badge.badge_image_url,
                "subscription_type": "bronze",
                "price": 5.0,
            }
        )

    silver_badge = None
    if has_silver:
        for mode_id in ["silver", "silver_badge"]:
            silver_badge = badge_map.get(mode_id)
            if silver_badge:
                break
        if not silver_badge:
            silver_badge = (
                db.query(TriviaModeConfig)
                .filter(
                    TriviaModeConfig.mode_name.ilike("%silver%"),
                    TriviaModeConfig.badge_image_url.isnot(None),
                )
                .first()
            )

    if silver_badge and silver_badge.badge_image_url:
        subscription_badges.append(
            {
                "id": silver_badge.mode_id,
                "name": silver_badge.mode_name,
                "image_url": silver_badge.badge_image_url,
                "subscription_type": "silver",
                "price": 10.0,
            }
        )

    # Get level and progress
from core.cache import default_cache
from core.config import CHAT_PROFILE_CACHE_SECONDS
from utils.user_level_service import get_level_progress

    level_progress = get_level_progress(user, db)

    return {
        "profile_pic_url": profile_pic_url,
        "avatar_url": avatar_url,
        "frame_url": frame_url,
        "badge": badge_info,  # Achievement badge
        "subscription_badges": subscription_badges,  # Array of subscription badge URLs
        "level": level_progress["level"],
        "level_progress": level_progress["progress"],  # e.g., "2/100", "120/200"
    }


def get_user_chat_profile_data_bulk(
    users: List[User], db: Session
) -> Dict[int, Dict[str, Any]]:
    """
    Batch version of get_user_chat_profile_data for multiple users.
    Returns a mapping of account_id to profile data.
    """
    if not users:
        return {}

    profile_cache: Dict[int, Dict[str, Any]] = {}
    missing_users = []
    for user in users:
        cache_key = f"chat_profile:{user.account_id}"
        cached = default_cache.get(cache_key)
        if cached is not None:
            profile_cache[user.account_id] = cached
        else:
            missing_users.append(user)

    if not missing_users:
        return profile_cache

    users = missing_users
    user_ids = [user.account_id for user in users]

    avatar_ids = [user.selected_avatar_id for user in users if user.selected_avatar_id]
    frame_ids = [user.selected_frame_id for user in users if user.selected_frame_id]
    badge_ids = {user.badge_id for user in users if user.badge_id}

    avatars = {}
    if avatar_ids:
        avatar_rows = db.query(Avatar).filter(Avatar.id.in_(avatar_ids)).all()
        avatars = {avatar.id: avatar for avatar in avatar_rows}

    frames = {}
    if frame_ids:
        frame_rows = db.query(Frame).filter(Frame.id.in_(frame_ids)).all()
        frames = {frame.id: frame for frame in frame_rows}

    badge_map = {}
    if badge_ids:
        badge_rows = (
            db.query(TriviaModeConfig)
            .filter(
                TriviaModeConfig.mode_id.in_(badge_ids),
                TriviaModeConfig.badge_image_url.isnot(None),
            )
            .all()
        )
        badge_map = {badge.mode_id: badge for badge in badge_rows}

    bronze_badge = None
    for mode_id in ["bronze", "bronze_badge", "brone_badge", "brone"]:
        bronze_badge = (
            db.query(TriviaModeConfig)
            .filter(
                TriviaModeConfig.mode_id == mode_id,
                TriviaModeConfig.badge_image_url.isnot(None),
            )
            .first()
        )
        if bronze_badge:
            break
    if not bronze_badge:
        bronze_badge = (
            db.query(TriviaModeConfig)
            .filter(
                TriviaModeConfig.mode_name.ilike("%bronze%"),
                TriviaModeConfig.badge_image_url.isnot(None),
            )
            .first()
        )

    silver_badge = None
    for mode_id in ["silver", "silver_badge"]:
        silver_badge = (
            db.query(TriviaModeConfig)
            .filter(
                TriviaModeConfig.mode_id == mode_id,
                TriviaModeConfig.badge_image_url.isnot(None),
            )
            .first()
        )
        if silver_badge:
            break
    if not silver_badge:
        silver_badge = (
            db.query(TriviaModeConfig)
            .filter(
                TriviaModeConfig.mode_name.ilike("%silver%"),
                TriviaModeConfig.badge_image_url.isnot(None),
            )
            .first()
        )

    active_subscriptions = (
        db.query(
            UserSubscription.user_id,
            SubscriptionPlan.unit_amount_minor,
            SubscriptionPlan.price_usd,
        )
        .join(SubscriptionPlan)
        .filter(
            and_(
                UserSubscription.user_id.in_(user_ids),
                UserSubscription.status == "active",
                UserSubscription.current_period_end > datetime.utcnow(),
                or_(
                    SubscriptionPlan.unit_amount_minor.in_([500, 1000]),
                    SubscriptionPlan.price_usd.in_([5.0, 10.0]),
                ),
            )
        )
        .all()
    )

    subscription_types = defaultdict(set)
    for user_id, unit_amount_minor, price_usd in active_subscriptions:
        if unit_amount_minor == 500 or price_usd == 5.0:
            subscription_types[user_id].add("bronze")
        if unit_amount_minor == 1000 or price_usd == 10.0:
            subscription_types[user_id].add("silver")

    from utils.user_level_service import get_level_progress_for_users

    level_progress_map = get_level_progress_for_users(users, db)

    for user in users:
        avatar_url = None
        if user.selected_avatar_id:
            avatar_obj = avatars.get(user.selected_avatar_id)
            if avatar_obj:
                bucket = getattr(avatar_obj, "bucket", None)
                object_key = getattr(avatar_obj, "object_key", None)
                if bucket and object_key:
                    try:
                        avatar_url = presign_get(bucket, object_key, expires=900)
                    except Exception as e:
                        logger.warning(
                            f"Failed to presign avatar {avatar_obj.id} for user {user.account_id}: {e}"
                        )
                else:
                    logger.debug(
                        f"Avatar {avatar_obj.id} missing bucket/object_key for user {user.account_id}"
                    )

        frame_url = None
        if user.selected_frame_id:
            frame_obj = frames.get(user.selected_frame_id)
            if frame_obj:
                bucket = getattr(frame_obj, "bucket", None)
                object_key = getattr(frame_obj, "object_key", None)
                if bucket and object_key:
                    try:
                        frame_url = presign_get(bucket, object_key, expires=900)
                    except Exception as e:
                        logger.warning(
                            f"Failed to presign frame {frame_obj.id} for user {user.account_id}: {e}"
                        )
                else:
                    logger.debug(
                        f"Frame {frame_obj.id} missing bucket/object_key for user {user.account_id}"
                    )

        badge_info = None
        if user.badge_id:
            mode_config = badge_map.get(user.badge_id)
            if mode_config and mode_config.badge_image_url:
                badge_info = {
                    "id": mode_config.mode_id,
                    "name": mode_config.mode_name,
                    "image_url": mode_config.badge_image_url,
                }

        subscription_badges = []
        user_subscription_types = subscription_types.get(user.account_id, set())
        if (
            "bronze" in user_subscription_types
            and bronze_badge
            and bronze_badge.badge_image_url
        ):
            subscription_badges.append(
                {
                    "id": bronze_badge.mode_id,
                    "name": bronze_badge.mode_name,
                    "image_url": bronze_badge.badge_image_url,
                    "subscription_type": "bronze",
                    "price": 5.0,
                }
            )
        if (
            "silver" in user_subscription_types
            and silver_badge
            and silver_badge.badge_image_url
        ):
            subscription_badges.append(
                {
                    "id": silver_badge.mode_id,
                    "name": silver_badge.mode_name,
                    "image_url": silver_badge.badge_image_url,
                    "subscription_type": "silver",
                    "price": 10.0,
                }
            )

        level_info = level_progress_map.get(
            user.account_id,
            {"level": user.level if user.level else 1, "level_progress": "0/100"},
        )

        profile = {
            "profile_pic_url": user.profile_pic_url,
            "avatar_url": avatar_url,
            "frame_url": frame_url,
            "badge": badge_info,
            "subscription_badges": subscription_badges,
            "level": level_info["level"],
            "level_progress": level_info["level_progress"],
        }
        profile_cache[user.account_id] = profile
        default_cache.set(
            f"chat_profile:{user.account_id}",
            profile,
            ttl_seconds=CHAT_PROFILE_CACHE_SECONDS,
        )

    return profile_cache
