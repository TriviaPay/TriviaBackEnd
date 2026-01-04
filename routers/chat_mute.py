from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from models import User
from routers.dependencies import get_current_user
from utils.chat_mute import (
    get_mute_preferences,
    is_chat_muted,
    add_muted_user,
    remove_muted_user,
    get_muted_users,
    get_muted_users_from_preferences
)
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat-mute", tags=["Chat Mute"])


class ToggleMuteRequest(BaseModel):
    muted: bool = True


@router.get("/preferences")
async def get_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get current user's chat mute preferences"""
    preferences = get_mute_preferences(current_user.account_id, db, create_if_missing=False)
    
    return {
        "global_chat_muted": preferences.global_chat_muted,
        "trivia_live_chat_muted": preferences.trivia_live_chat_muted,
        "private_chat_muted_users": get_muted_users_from_preferences(preferences)
    }


@router.post("/global")
async def toggle_global_chat_mute(
    request: ToggleMuteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Toggle mute for global chat"""
    preferences = get_mute_preferences(current_user.account_id, db)
    preferences.global_chat_muted = request.muted
    db.commit()
    
    return {
        "message": "Global chat muted" if request.muted else "Global chat unmuted",
        "global_chat_muted": preferences.global_chat_muted
    }


@router.post("/trivia-live")
async def toggle_trivia_live_chat_mute(
    request: ToggleMuteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Toggle mute for trivia live chat"""
    preferences = get_mute_preferences(current_user.account_id, db)
    preferences.trivia_live_chat_muted = request.muted
    db.commit()
    
    return {
        "message": "Trivia live chat muted" if request.muted else "Trivia live chat unmuted",
        "trivia_live_chat_muted": preferences.trivia_live_chat_muted
    }


@router.post("/private/{user_id}")
async def toggle_private_chat_mute(
    user_id: int = Path(..., description="User ID to mute/unmute"),
    request: ToggleMuteRequest = ToggleMuteRequest(muted=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Mute or unmute a specific user for private chat"""
    if user_id == current_user.account_id:
        raise HTTPException(status_code=400, detail="Cannot mute yourself")
    
    # Verify user exists
    target_user = db.query(User).filter(User.account_id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if request.muted:
        add_muted_user(current_user.account_id, user_id, db)
        return {
            "message": f"User {user_id} muted for private chat",
            "muted": True
        }
    else:
        remove_muted_user(current_user.account_id, user_id, db)
        return {
            "message": f"User {user_id} unmuted for private chat",
            "muted": False
        }


@router.get("/private")
async def list_muted_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List all users muted for private chat"""
    muted_user_ids = get_muted_users(current_user.account_id, db)
    muted_users = []
    if muted_user_ids:
        # Get user details for muted users in one query
        from models import User
        users = db.query(User).filter(User.account_id.in_(muted_user_ids)).all()
        user_map = {user.account_id: user for user in users}
        for user_id in muted_user_ids:
            user = user_map.get(user_id)
            if user:
                muted_users.append({
                    "user_id": user.account_id,
                    "username": user.username or f"User{user.account_id}",
                    "profile_pic_url": user.profile_pic_url
                })
    
    return {
        "muted_users": muted_users,
        "count": len(muted_users)
    }
