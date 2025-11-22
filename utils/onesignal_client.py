import httpx
from config import ONESIGNAL_ENABLED, ONESIGNAL_APP_ID, ONESIGNAL_REST_API_KEY
from sqlalchemy.orm import Session
from models import OneSignalPlayer
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

ONESIGNAL_API_URL = "https://onesignal.com/api/v1/notifications"
ONESIGNAL_ACTIVITY_THRESHOLD_SECONDS = 30  # Don't send push if user was active in last 30 seconds


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
    
    # Prepare data payload with in-app notification flag
    notification_data = data.copy() if data else {}
    if is_in_app_notification:
        notification_data["show_as_in_app"] = True
    
    payload = {
        "app_id": ONESIGNAL_APP_ID,
        "include_player_ids": player_ids,
        "headings": {"en": heading},
        "contents": {"en": content},
    }
    
    if notification_data:
        payload["data"] = notification_data
    
    if url:
        payload["url"] = url
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {ONESIGNAL_REST_API_KEY}"
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(ONESIGNAL_API_URL, json=payload, headers=headers)
            response.raise_for_status()
            
            result = response.json()
            # Check for invalid player IDs in response
            if "invalid_player_ids" in result and result["invalid_player_ids"]:
                logger.warning(f"OneSignal reported invalid player IDs: {result['invalid_player_ids']}")
                # Note: We could mark these as invalid here, but we'll do it in a separate cleanup task
            
            logger.info(f"OneSignal notification sent to {len(player_ids)} players")
            return True
    except httpx.HTTPStatusError as e:
        logger.error(f"OneSignal API error: {e.response.status_code} - {e.response.text}")
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

