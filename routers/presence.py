from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Optional
import logging

from db import get_db
from models import User, UserPresence
from routers.dependencies import get_current_user
from config import PRESENCE_ENABLED

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/presence", tags=["Presence"])


class UpdatePresenceRequest(BaseModel):
    share_last_seen: Optional[str] = Field(None, pattern="^(everyone|all|contacts|nobody)$", example="contacts")
    share_online: Optional[bool] = Field(None, example=True)
    read_receipts: Optional[bool] = Field(None, example=True)
    
    class Config:
        json_schema_extra = {
            "example": {
                "share_last_seen": "contacts",
                "share_online": True,
                "read_receipts": True
            }
        }


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
        return {
            "user_id": current_user.account_id,
            "last_seen_at": None,
            "device_online": False,
            "privacy_settings": {
                "share_last_seen": "contacts",
                "share_online": True,
                "read_receipts": True
            }
        }
    
    privacy = presence.privacy_settings or {}
    share_last_seen = privacy.get("share_last_seen", "contacts")
    if share_last_seen == "all":
        share_last_seen = "everyone"
    
    return {
        "user_id": current_user.account_id,
        "last_seen_at": presence.last_seen_at.isoformat() if presence.last_seen_at else None,
        "device_online": presence.device_online,
        "privacy_settings": {
            "share_last_seen": share_last_seen,
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
            privacy_settings={
                "share_last_seen": "contacts",
                "share_online": True,
                "read_receipts": True
            }
        )
        db.add(presence)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            presence = db.query(UserPresence).filter(
                UserPresence.user_id == current_user.account_id
            ).first()
            if not presence:
                raise HTTPException(status_code=500, detail="Failed to update presence")
    
    privacy = dict(presence.privacy_settings or {
        "share_last_seen": "contacts",
        "share_online": True,
        "read_receipts": True
    })
    
    if request.share_last_seen is not None:
        share_last_seen = "everyone" if request.share_last_seen == "all" else request.share_last_seen
        privacy["share_last_seen"] = share_last_seen
    
    if request.share_online is not None:
        privacy["share_online"] = request.share_online
    
    if request.read_receipts is not None:
        privacy["read_receipts"] = request.read_receipts

    presence.privacy_settings = privacy
    
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
