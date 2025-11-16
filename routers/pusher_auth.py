from fastapi import APIRouter, Depends, HTTPException, status, Form
from sqlalchemy.orm import Session
from typing import Optional
import logging

from db import get_db
from routers.dependencies import get_current_user
from models import User, PrivateChatConversation
from config import PUSHER_ENABLED, PUSHER_SECRET, PUSHER_KEY
from utils.pusher_client import get_pusher_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pusher", tags=["Pusher"])


@router.post("/auth")
async def pusher_auth(
    socket_id: str = Form(...),
    channel_name: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Authenticate Pusher channel subscription.
    
    For private channels (private-conversation-{id}): verify user is conversation participant
    For presence channels: allow if authenticated
    For public channels: allow if authenticated
    """
    if not PUSHER_ENABLED:
        raise HTTPException(status_code=403, detail="Pusher is not enabled")
    
    pusher_client = get_pusher_client()
    if not pusher_client:
        raise HTTPException(status_code=500, detail="Pusher client not available")
    
    # Handle private conversation channels
    if channel_name.startswith("private-conversation-"):
        try:
            conversation_id = int(channel_name.split("-")[-1])
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Invalid channel name format")
        
        # Verify user is a participant in this conversation
        conversation = db.query(PrivateChatConversation).filter(
            PrivateChatConversation.id == conversation_id
        ).first()
        
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
            raise HTTPException(status_code=403, detail="Not authorized for this conversation")
        
        # Authenticate private channel
        auth = pusher_client.authenticate(
            channel=channel_name,
            socket_id=socket_id
        )
        return auth
    
    # Handle presence channels
    elif channel_name.startswith("presence-"):
        # For presence channels, include user info
        user_info = {
            "user_id": current_user.account_id,
            "username": current_user.username or current_user.email.split('@')[0] if current_user.email else f"User{current_user.account_id}",
        }
        
        auth = pusher_client.authenticate(
            channel=channel_name,
            socket_id=socket_id,
            custom_data=user_info
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

