import logging
import os
import time
from typing import Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlalchemy.orm import Session

from config import PUSHER_ENABLED, PUSHER_KEY, PUSHER_SECRET
from db import get_db
from models import PrivateChatConversation, PrivateChatStatus, User
from routers.dependencies import get_current_user
from utils.chat_blocking import check_blocked
from utils.pusher_client import get_pusher_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pusher", tags=["Pusher"])
_AUTH_CACHE_TTL_SECONDS = int(os.getenv("PUSHER_AUTH_CACHE_TTL_SECONDS", "5"))
_conversation_cache: Dict[int, Tuple[int, int, str, float]] = {}
_block_cache: Dict[str, Tuple[bool, float]] = {}


def _cache_get_conversation(conversation_id: int) -> Optional[Tuple[int, int, str]]:
    cached = _conversation_cache.get(conversation_id)
    if not cached:
        return None
    user1_id, user2_id, status, expires_at = cached
    if expires_at > time.time():
        return user1_id, user2_id, status
    _conversation_cache.pop(conversation_id, None)
    return None


def _cache_set_conversation(
    conversation_id: int, user1_id: int, user2_id: int, status: str
) -> None:
    if _AUTH_CACHE_TTL_SECONDS <= 0:
        return
    _conversation_cache[conversation_id] = (
        user1_id,
        user2_id,
        status,
        time.time() + _AUTH_CACHE_TTL_SECONDS,
    )


def _block_cache_key(user1_id: int, user2_id: int) -> str:
    return f"{min(user1_id, user2_id)}:{max(user1_id, user2_id)}"


def _cache_get_blocked(user1_id: int, user2_id: int) -> Optional[bool]:
    cached = _block_cache.get(_block_cache_key(user1_id, user2_id))
    if not cached:
        return None
    blocked, expires_at = cached
    if expires_at > time.time():
        return blocked
    _block_cache.pop(_block_cache_key(user1_id, user2_id), None)
    return None


def _cache_set_blocked(user1_id: int, user2_id: int, blocked: bool) -> None:
    if _AUTH_CACHE_TTL_SECONDS <= 0:
        return
    _block_cache[_block_cache_key(user1_id, user2_id)] = (
        blocked,
        time.time() + _AUTH_CACHE_TTL_SECONDS,
    )


def _presence_channel_user_id(channel_name: str) -> Optional[int]:
    if channel_name.startswith("presence-user-"):
        suffix = channel_name[len("presence-user-") :]
    elif channel_name.startswith("presence-"):
        suffix = channel_name[len("presence-") :]
    else:
        return None
    if suffix.isdigit():
        return int(suffix)
    return None


@router.post("/auth")
async def pusher_auth(
    socket_id: str = Form(...),
    channel_name: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Authenticate Pusher channel subscription.

    For private channels (private-conversation-{id}): verify user is conversation participant
    For presence channels: allow if authenticated
    For public channels: allow if authenticated
    """
    if not PUSHER_ENABLED:
        raise HTTPException(status_code=403, detail="Pusher is not enabled")

    # Handle private conversation channels
    if channel_name.startswith("private-conversation-"):
        pusher_client = get_pusher_client()
        if not pusher_client:
            raise HTTPException(status_code=500, detail="Pusher client not available")
        try:
            conversation_id = int(channel_name.split("-")[-1])
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Invalid channel name format")

        # Verify user is a participant in this conversation
        cached_conversation = _cache_get_conversation(conversation_id)
        if cached_conversation:
            user1_id, user2_id, status = cached_conversation
        else:
            conversation = (
                db.query(
                    PrivateChatConversation.user1_id,
                    PrivateChatConversation.user2_id,
                    PrivateChatConversation.status,
                )
                .filter(PrivateChatConversation.id == conversation_id)
                .first()
            )
            if not conversation:
                raise HTTPException(status_code=404, detail="Conversation not found")
            user1_id, user2_id, status = conversation
            _cache_set_conversation(conversation_id, user1_id, user2_id, status)

        if current_user.account_id not in [user1_id, user2_id]:
            raise HTTPException(
                status_code=403, detail="Not authorized for this conversation"
            )

        # Check conversation status - must be ACCEPTED
        if status != "accepted":
            raise HTTPException(status_code=403, detail="Conversation not accepted")

        # Check if users are blocked
        blocked = _cache_get_blocked(user1_id, user2_id)
        if blocked is None:
            blocked = check_blocked(db, user1_id, user2_id)
            _cache_set_blocked(user1_id, user2_id, blocked)
        if blocked:
            raise HTTPException(status_code=403, detail="Users are blocked")

        # Authenticate private channel
        auth = pusher_client.authenticate(channel=channel_name, socket_id=socket_id)
        return auth

    # Handle presence channels
    elif channel_name.startswith("presence-"):
        scoped_user_id = _presence_channel_user_id(channel_name)
        if scoped_user_id is not None and scoped_user_id != current_user.account_id:
            raise HTTPException(
                status_code=403, detail="Not authorized for this presence channel"
            )

        pusher_client = get_pusher_client()
        if not pusher_client:
            raise HTTPException(status_code=500, detail="Pusher client not available")

        # For presence channels, include user info
        user_info = {
            "user_id": current_user.account_id,
            "username": (
                current_user.username or current_user.email.split("@")[0]
                if current_user.email
                else f"User{current_user.account_id}"
            ),
        }

        auth = pusher_client.authenticate(
            channel=channel_name, socket_id=socket_id, custom_data=user_info
        )
        return auth

    # Handle public channels (global-chat, trivia-live-chat)
    elif channel_name in ["global-chat", "trivia-live-chat"]:
        # Public channels don't require authentication in Pusher, but we verify user is authenticated
        # Return empty response or simple confirmation
        # Client can subscribe directly to public channels without auth endpoint
        return {"status": "authorized"}

    else:
        raise HTTPException(status_code=400, detail="Unknown channel type")
