from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional
import logging

from db import get_db
from models import User, UserPresence
from routers.dependencies import get_current_user
from config import PRESENCE_ENABLED

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/presence", tags=["Presence"])


class UpdatePresenceRequest(BaseModel):
    share_last_seen: Optional[str] = Field(None, pattern="^(all|contacts|nobody)$")
    share_online: Optional[bool] = None
    read_receipts: Optional[bool] = None


@router.get("")
async def get_my_presence(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get my presence settings."""
    if not PRESENCE_ENABLED:
        raise HTTPException(status_code=403, detail="Presence feature is not enabled")
    
    presence = db.query(UserPresence).filter(
        UserPresence.user_id == current_user.account_id
    ).first()
    
    if not presence:
        # Create default presence
        presence = UserPresence(
            user_id=current_user.account_id,
            privacy_settings={
                "share_last_seen": "contacts",
                "share_online": True,
                "read_receipts": True
            }
        )
        db.add(presence)
        db.commit()
        db.refresh(presence)
    
    privacy = presence.privacy_settings or {}
    
    return {
        "user_id": current_user.account_id,
        "last_seen_at": presence.last_seen_at.isoformat() if presence.last_seen_at else None,
        "device_online": presence.device_online,
        "privacy_settings": {
            "share_last_seen": privacy.get("share_last_seen", "contacts"),
            "share_online": privacy.get("share_online", True),
            "read_receipts": privacy.get("read_receipts", True)
        }
    }


@router.patch("")
async def update_presence(
    request: UpdatePresenceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update privacy settings."""
    if not PRESENCE_ENABLED:
        raise HTTPException(status_code=403, detail="Presence feature is not enabled")
    
    presence = db.query(UserPresence).filter(
        UserPresence.user_id == current_user.account_id
    ).first()
    
    if not presence:
        presence = UserPresence(
            user_id=current_user.account_id,
            privacy_settings={}
        )
        db.add(presence)
    
    if presence.privacy_settings is None:
        presence.privacy_settings = {}
    
    if request.share_last_seen is not None:
        presence.privacy_settings["share_last_seen"] = request.share_last_seen
    
    if request.share_online is not None:
        presence.privacy_settings["share_online"] = request.share_online
    
    if request.read_receipts is not None:
        presence.privacy_settings["read_receipts"] = request.read_receipts
    
    try:
        db.commit()
        db.refresh(presence)
        
        return {
            "privacy_settings": presence.privacy_settings
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating presence: {e}")
        raise HTTPException(status_code=500, detail="Failed to update presence")

