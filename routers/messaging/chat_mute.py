from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from db import get_db
from models import User
from routers.dependencies import get_current_user
from .schemas import ToggleMuteRequest
from .service import (
    get_chat_mute_preferences as service_get_chat_mute_preferences,
    list_private_chat_muted_users as service_list_private_chat_muted_users,
    set_global_chat_mute as service_set_global_chat_mute,
    set_private_chat_mute as service_set_private_chat_mute,
    set_trivia_live_chat_mute as service_set_trivia_live_chat_mute,
)

router = APIRouter(prefix="/chat-mute", tags=["Chat Mute"])


@router.get("/preferences")
async def get_preferences(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """Get current user's chat mute preferences"""
    return service_get_chat_mute_preferences(db, current_user=current_user)


@router.post("/global")
async def toggle_global_chat_mute(
    request: ToggleMuteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Toggle mute for global chat"""
    return service_set_global_chat_mute(
        db, current_user=current_user, muted=request.muted
    )


@router.post("/trivia-live")
async def toggle_trivia_live_chat_mute(
    request: ToggleMuteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Toggle mute for trivia live chat"""
    return service_set_trivia_live_chat_mute(
        db, current_user=current_user, muted=request.muted
    )


@router.post("/private/{user_id}")
async def toggle_private_chat_mute(
    user_id: int = Path(..., description="User ID to mute/unmute"),
    request: ToggleMuteRequest = ToggleMuteRequest(muted=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mute or unmute a specific user for private chat"""
    return service_set_private_chat_mute(
        db, current_user=current_user, user_id=user_id, muted=request.muted
    )


@router.get("/private")
async def list_muted_users(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """List all users muted for private chat"""
    return service_list_private_chat_muted_users(db, current_user=current_user)
