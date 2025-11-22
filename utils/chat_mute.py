"""
Helper functions for chat mute preferences.
"""
from typing import List, Optional
from sqlalchemy.orm import Session
import logging

from models import ChatMutePreferences, User

logger = logging.getLogger(__name__)


def get_mute_preferences(user_id: int, db: Session) -> ChatMutePreferences:
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
        preferences = ChatMutePreferences(
            user_id=user_id,
            global_chat_muted=False,
            trivia_live_chat_muted=False,
            private_chat_muted_users=None
        )
        db.add(preferences)
        db.commit()
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
    preferences = get_mute_preferences(user_id, db)
    
    if chat_type == 'global':
        return preferences.global_chat_muted
    elif chat_type == 'trivia_live':
        return preferences.trivia_live_chat_muted
    else:
        logger.warning(f"Unknown chat type: {chat_type}")
        return False


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
    preferences = get_mute_preferences(muted_by_user_id, db)
    
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
    preferences = get_mute_preferences(user_id, db)
    
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
    preferences = get_mute_preferences(user_id, db)
    
    muted_users = preferences.private_chat_muted_users or []
    if not isinstance(muted_users, list):
        muted_users = []
    
    if unmuted_user_id in muted_users:
        muted_users.remove(unmuted_user_id)
        preferences.private_chat_muted_users = muted_users if muted_users else None
        db.commit()


def get_muted_users(user_id: int, db: Session) -> List[int]:
    """
    Get list of user IDs that are muted for private chat.
    
    Args:
        user_id: User account ID
        db: Database session
        
    Returns:
        List of muted user IDs
    """
    preferences = get_mute_preferences(user_id, db)
    
    if not preferences.private_chat_muted_users:
        return []
    
    muted_users = preferences.private_chat_muted_users
    if isinstance(muted_users, list):
        return muted_users
    
    return []

