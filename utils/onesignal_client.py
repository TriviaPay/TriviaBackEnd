import httpx
from config import ONESIGNAL_ENABLED, ONESIGNAL_APP_ID, ONESIGNAL_REST_API_KEY
from sqlalchemy.orm import Session
from models import OneSignalPlayer
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

ONESIGNAL_API_URL = "https://api.onesignal.com/notifications"
ONESIGNAL_ACTIVITY_THRESHOLD_SECONDS = 30  # Don't send push if user was active in last 30 seconds

# Log OneSignal configuration on module import (one-time, at server startup)
if ONESIGNAL_ENABLED:
    app_id_len = len(ONESIGNAL_APP_ID) if ONESIGNAL_APP_ID else 0
    rest_key_len = len(ONESIGNAL_REST_API_KEY) if ONESIGNAL_REST_API_KEY else 0
    logger.info(
        f"OneSignal configured: ENABLED=True, "
        f"APP_ID_SET={bool(ONESIGNAL_APP_ID)} (len={app_id_len}, expected ~36 for UUID), "
        f"REST_KEY_SET={bool(ONESIGNAL_REST_API_KEY)} (len={rest_key_len}, expected ~40-50 for legacy or ~110 for v2)"
    )
    # Warn if key lengths look suspicious
    if ONESIGNAL_APP_ID and app_id_len != 36:
        logger.warning(f"⚠️ OneSignal APP_ID length is {app_id_len}, expected 36 (UUID format). Verify you're using App ID, not REST API Key.")
    # v2 keys are ~110 chars, legacy keys are ~40-50
    if ONESIGNAL_REST_API_KEY and rest_key_len < 30:
        logger.warning(f"⚠️ OneSignal REST_API_KEY length is {rest_key_len}, seems too short. Verify you copied the full key.")
    # Detect v2 key format (starts with os_v2_app_)
    is_v2_key = ONESIGNAL_REST_API_KEY and ONESIGNAL_REST_API_KEY.startswith("os_v2_app_")
    if is_v2_key:
        logger.info(f"✅ Detected OneSignal v2 App API Key (os_v2_app_...). Using v2 API format (Authorization: Key, URL: api.onesignal.com)")
    elif ONESIGNAL_REST_API_KEY and rest_key_len > 100:
        logger.warning(f"⚠️ OneSignal REST_API_KEY length is {rest_key_len} but doesn't start with 'os_v2_app_'. Verify key format.")
else:
    logger.info("OneSignal is DISABLED (ONESIGNAL_ENABLED=False)")


async def send_push_notification_async(
    player_ids: List[str],
    heading: str,
    content: str,
    data: Optional[Dict[str, Any]] = None,
    url: Optional[str] = None,
    is_in_app_notification: bool = False
) -> bool:
    """
    Send push notification via OneSignal asynchronously.
    
    Args:
        player_ids: List of OneSignal player IDs to send to
        heading: Notification heading/title
        content: Notification content/message
        data: Optional data payload to include
        url: Optional URL to open when notification is clicked
        is_in_app_notification: If True, adds show_as_in_app flag for frontend to display as in-app notification
    """
    if not ONESIGNAL_ENABLED:
        logger.debug("OneSignal not enabled, notification not sent")
        return False
    
    if not player_ids:
        return False
    
    if not all([ONESIGNAL_APP_ID, ONESIGNAL_REST_API_KEY]):
        logger.warning("OneSignal credentials not fully configured")
        return False
    
    # Debug logging: Verify credentials are loaded (but don't log the actual key value)
    logger.debug(
        f"OneSignal config check: ENABLED={ONESIGNAL_ENABLED}, "
        f"APP_ID_SET={bool(ONESIGNAL_APP_ID)}, REST_KEY_SET={bool(ONESIGNAL_REST_API_KEY)}, "
        f"APP_ID_LEN={len(ONESIGNAL_APP_ID) if ONESIGNAL_APP_ID else 0}, "
        f"REST_KEY_LEN={len(ONESIGNAL_REST_API_KEY) if ONESIGNAL_REST_API_KEY else 0}"
    )
    
    # Prepare data payload with in-app notification flag
    notification_data = data.copy() if data else {}
    if is_in_app_notification:
        notification_data["show_as_in_app"] = True
        # For in-app notifications, we also need to ensure OneSignal doesn't suppress them
        # when app is in foreground. We'll use content_available and set send_after to now
        logger.debug(f"In-app notification flag set: show_as_in_app=True for {len(player_ids)} players")
    
    payload = {
        "app_id": ONESIGNAL_APP_ID,
        "include_player_ids": player_ids,
        "headings": {"en": heading},
        "contents": {"en": content},
    }
    
    # For in-app notifications, add content_available to ensure delivery even when app is in foreground
    if is_in_app_notification:
        payload["content_available"] = True
        # iOS requires this for background notifications
        payload["ios_badgeType"] = "None"  # Don't update badge for in-app
        payload["ios_badgeCount"] = 0
    
    if notification_data:
        payload["data"] = notification_data
    
    if url:
        payload["url"] = url
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Key {ONESIGNAL_REST_API_KEY}"
    }
    
    # Detailed debug logging for auth diagnostics
    logger.debug(
        f"OneSignal request debug: "
        f"URL={ONESIGNAL_API_URL}, "
        f"app_id_set={bool(ONESIGNAL_APP_ID)}, "
        f"app_id_full_len={len(ONESIGNAL_APP_ID) if ONESIGNAL_APP_ID else 0}, "
        f"rest_key_len={len(ONESIGNAL_REST_API_KEY) if ONESIGNAL_REST_API_KEY else 0}, "
        f"player_count={len(player_ids)}"
    )
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(ONESIGNAL_API_URL, json=payload, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            # Check for invalid player IDs in response
            if "invalid_player_ids" in result and result["invalid_player_ids"]:
                logger.warning(f"OneSignal reported invalid player IDs: {result['invalid_player_ids']}")
                # Note: We could mark these as invalid here, but we'll do it in a separate cleanup task
            
            notification_type = "in-app" if is_in_app_notification else "system"
            logger.info(
                f"✅ OneSignal {notification_type} notification sent successfully to {len(player_ids)} players | "
                f"show_as_in_app={is_in_app_notification} | notification_id={result.get('id', 'N/A')}"
            )
            if "id" in result:
                logger.debug(f"OneSignal notification ID: {result['id']}")
            
            # Log the data payload to verify show_as_in_app flag is included
            if is_in_app_notification and notification_data:
                logger.debug(f"In-app notification payload includes: show_as_in_app={notification_data.get('show_as_in_app', False)}")
            
            return True
    except httpx.HTTPStatusError as e:
        # Avoid logging raw response bodies; they may contain sensitive or identifying information.
        content_type = e.response.headers.get("content-type", "unknown")
        logger.error(
            f"❌ OneSignal API error: status={e.response.status_code}, content_type={content_type}, "
            f"requested_players={len(player_ids)}, url={ONESIGNAL_API_URL}"
        )
        return False
    except Exception as e:
        logger.error(f"Failed to send OneSignal notification: {e}")
        return False


def should_send_push(user_id: int, db: Session) -> bool:
    """
    Check if user is active (recent activity) to avoid sending push notifications.
    Returns True if push should be sent (user is not active), False otherwise.
    """
    # Check if user has recent activity (last_active within threshold)
    threshold_time = datetime.utcnow() - timedelta(seconds=ONESIGNAL_ACTIVITY_THRESHOLD_SECONDS)
    
    active_player = db.query(OneSignalPlayer).filter(
        OneSignalPlayer.user_id == user_id,
        OneSignalPlayer.is_valid == True,
        OneSignalPlayer.last_active >= threshold_time
    ).first()
    
    if active_player:
        logger.debug(f"User {user_id} is active (last_active: {active_player.last_active}), skipping push")
        return False
    
    return True


def is_user_active(user_id: int, db: Session) -> bool:
    """
    Check if user is active (recent activity within threshold).
    Returns True if user is active, False otherwise.
    This is the inverse of should_send_push().
    """
    return not should_send_push(user_id, db)


def mark_player_invalid(player_id: str, db: Session) -> None:
    """Mark a OneSignal player as invalid (e.g., app uninstalled)"""
    player = db.query(OneSignalPlayer).filter(OneSignalPlayer.player_id == player_id).first()
    if player:
        player.is_valid = False
        player.last_failure_at = datetime.utcnow()
        db.commit()
        logger.info(f"Marked OneSignal player {player_id} as invalid")


def cleanup_invalid_players(db: Session, days_old: int = 30) -> int:
    """
    Clean up invalid players that have been invalid for more than specified days.
    Returns number of players deleted.
    """
    cutoff_date = datetime.utcnow() - timedelta(days=days_old)
    
    deleted_count = db.query(OneSignalPlayer).filter(
        OneSignalPlayer.is_valid == False,
        OneSignalPlayer.last_failure_at < cutoff_date
    ).delete()
    
    db.commit()
    logger.info(f"Cleaned up {deleted_count} invalid OneSignal players")
    return deleted_count


def get_user_player_ids(user_id: int, db: Session, valid_only: bool = True) -> List[str]:
    """Get all player IDs for a user"""
    query = db.query(OneSignalPlayer.player_id).filter(
        OneSignalPlayer.user_id == user_id
    )
    
    if valid_only:
        query = query.filter(OneSignalPlayer.is_valid == True)
    
    players = query.all()
    return [p[0] for p in players]
