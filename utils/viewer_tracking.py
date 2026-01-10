"""
Viewer tracking utilities for live chat.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from models import LiveChatViewer

logger = logging.getLogger(__name__)

ACTIVE_WINDOW_MINUTES = 5


async def mark_viewer_seen(session_id: int, user_id: int, db: Session) -> None:
    """
    Upsert viewer row: set last_seen = now() and is_active = True.

    Args:
        session_id: Session ID
        user_id: User account ID
        db: Database session
    """
    try:
        now = datetime.utcnow()
        existing_viewer = (
            db.query(LiveChatViewer)
            .filter(
                LiveChatViewer.session_id == session_id,
                LiveChatViewer.user_id == user_id,
            )
            .first()
        )

        if existing_viewer:
            existing_viewer.last_seen = now
            existing_viewer.is_active = True
        else:
            new_viewer = LiveChatViewer(
                session_id=session_id,
                user_id=user_id,
                joined_at=now,
                last_seen=now,
                is_active=True,
            )
            db.add(new_viewer)

        db.commit()
        logger.debug(f"Marked viewer {user_id} as seen for session {session_id}")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to mark viewer {user_id} as seen: {e}")
        raise


def get_active_viewer_count(session_id: int, db: Session) -> int:
    """
    Count viewers with last_seen within ACTIVE_WINDOW_MINUTES.

    Args:
        session_id: Session ID
        db: Database session

    Returns:
        Count of active viewers
    """
    try:
        cutoff_time = datetime.utcnow() - timedelta(minutes=ACTIVE_WINDOW_MINUTES)
        count = (
            db.query(LiveChatViewer)
            .filter(
                LiveChatViewer.session_id == session_id,
                LiveChatViewer.is_active.is_(True),
                LiveChatViewer.last_seen >= cutoff_time,
            )
            .count()
        )
        return count
    except Exception as e:
        logger.error(f"Failed to get active viewer count for session {session_id}: {e}")
        return 0
