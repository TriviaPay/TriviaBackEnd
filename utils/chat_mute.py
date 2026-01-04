"""
Helper functions for chat mute preferences.
"""
from typing import List, Optional, Set
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import logging

from models import ChatMutePreferences

logger = logging.getLogger(__name__)


def get_mute_preferences(
    user_id: int,
    db: Session,
    create_if_missing: bool = True
) -> ChatMutePreferences:
    """
    Get or create mute preferences for a user.
    
    Args:
        user_id: User account ID
        db: Database session
        
    Returns:
        ChatMutePreferences object
    """
    preferences = db.query(ChatMutePreferences).filter(
        ChatMutePreferences.user_id == user_id
    ).first()
    
    if not preferences:
        if not create_if_missing:
            return ChatMutePreferences(
                user_id=user_id,
                global_chat_muted=False,
                trivia_live_chat_muted=False,
                private_chat_muted_users=None
            )
        preferences = ChatMutePreferences(
            user_id=user_id,
            global_chat_muted=False,
            trivia_live_chat_muted=False,
            private_chat_muted_users=None
        )
        db.add(preferences)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            preferences = db.query(ChatMutePreferences).filter(
                ChatMutePreferences.user_id == user_id
            ).first()
            if preferences:
                return preferences
            raise
        db.refresh(preferences)
    
    return preferences


def is_chat_muted(user_id: int, chat_type: str, db: Session) -> bool:
    """
    Check if user has muted a specific chat type.
    
    Args:
        user_id: User account ID
        chat_type: 'global' or 'trivia_live'
        db: Database session
        
    Returns:
        True if muted, False otherwise
    """
    preferences = get_mute_preferences(user_id, db, create_if_missing=False)
    
    if chat_type == 'global':
        return preferences.global_chat_muted
    elif chat_type == 'trivia_live':
        return preferences.trivia_live_chat_muted
    else:
        logger.warning(f"Unknown chat type: {chat_type}")
        return False


def get_muted_user_ids(user_ids: List[int], chat_type: str, db: Session) -> Set[int]:
    """
    Batch lookup for muted users for a given chat type.
    Does not create missing preference rows.
    """
    if not user_ids:
        return set()

    query = db.query(ChatMutePreferences.user_id).filter(
        ChatMutePreferences.user_id.in_(user_ids)
    )

    if chat_type == 'global':
        query = query.filter(ChatMutePreferences.global_chat_muted.is_(True))
    elif chat_type == 'trivia_live':
        query = query.filter(ChatMutePreferences.trivia_live_chat_muted.is_(True))
    else:
        logger.warning(f"Unknown chat type: {chat_type}")
        return set()

    return {row[0] for row in query.all()}


def is_user_muted_for_private_chat(user_id: int, muted_by_user_id: int, db: Session) -> bool:
    """
    Check if a user is muted by another user for private chat.
    
    Args:
        user_id: User ID to check if muted
        muted_by_user_id: User ID who may have muted the other user
        db: Database session
        
    Returns:
        True if user_id is muted by muted_by_user_id, False otherwise
    """
    preferences = get_mute_preferences(muted_by_user_id, db, create_if_missing=False)
    
    if not preferences.private_chat_muted_users:
        return False
    
    muted_users = preferences.private_chat_muted_users
    if isinstance(muted_users, list):
        return user_id in muted_users
    
    return False


def add_muted_user(user_id: int, muted_user_id: int, db: Session) -> None:
    """
    Add a user to the muted users list for private chat.
    
    Args:
        user_id: User who is muting
        muted_user_id: User to mute
        db: Database session
    """
    preferences = _get_preferences_for_update(user_id, db)
    
    muted_users = preferences.private_chat_muted_users or []
    if not isinstance(muted_users, list):
        muted_users = []
    
    if muted_user_id not in muted_users:
        muted_users.append(muted_user_id)
        preferences.private_chat_muted_users = muted_users
        db.commit()


def remove_muted_user(user_id: int, unmuted_user_id: int, db: Session) -> None:
    """
    Remove a user from the muted users list for private chat.
    
    Args:
        user_id: User who is unmuting
        unmuted_user_id: User to unmute
        db: Database session
    """
    preferences = _get_preferences_for_update(user_id, db)
    
    muted_users = preferences.private_chat_muted_users or []
    if not isinstance(muted_users, list):
        muted_users = []
    
    if unmuted_user_id in muted_users:
        muted_users.remove(unmuted_user_id)
        preferences.private_chat_muted_users = muted_users if muted_users else None
        db.commit()


def get_muted_users_from_preferences(preferences: ChatMutePreferences) -> List[int]:
    if not preferences or not preferences.private_chat_muted_users:
        return []
    muted_users = preferences.private_chat_muted_users
    if isinstance(muted_users, list):
        return muted_users
    return []


def get_muted_users(user_id: int, db: Session) -> List[int]:
    """
    Get list of user IDs that are muted for private chat.
    
    Args:
        user_id: User account ID
        db: Database session
        
    Returns:
        List of muted user IDs
    """
    preferences = get_mute_preferences(user_id, db, create_if_missing=False)
    return get_muted_users_from_preferences(preferences)
def _get_preferences_for_update(user_id: int, db: Session) -> ChatMutePreferences:
    """
    Get or create preferences with a row lock to avoid lost updates.
    """
    preferences = db.query(ChatMutePreferences).filter(
        ChatMutePreferences.user_id == user_id
    ).with_for_update().first()
    if preferences:
        return preferences

    preferences = ChatMutePreferences(
        user_id=user_id,
        global_chat_muted=False,
        trivia_live_chat_muted=False,
        private_chat_muted_users=None
    )
    db.add(preferences)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    preferences = db.query(ChatMutePreferences).filter(
        ChatMutePreferences.user_id == user_id
    ).with_for_update().first()
    if preferences:
        return preferences

    preferences = ChatMutePreferences(
        user_id=user_id,
        global_chat_muted=False,
        trivia_live_chat_muted=False,
        private_chat_muted_users=None
    )
    db.add(preferences)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    preferences = db.query(ChatMutePreferences).filter(
        ChatMutePreferences.user_id == user_id
    ).with_for_update().first()
    return preferences
