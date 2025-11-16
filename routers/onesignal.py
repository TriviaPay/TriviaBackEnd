from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from datetime import datetime

from db import get_db
from models import User, OneSignalPlayer
from routers.dependencies import get_current_user
from config import ONESIGNAL_ENABLED
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onesignal", tags=["OneSignal"])


class RegisterPlayerRequest(BaseModel):
    player_id: str = Field(..., description="OneSignal player ID")
    platform: str = Field(..., description="Platform: 'ios', 'android', or 'web'")


@router.post("/register")
async def register_player(
    request: RegisterPlayerRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Register or update OneSignal player ID for push notifications"""
    if not ONESIGNAL_ENABLED:
        raise HTTPException(status_code=403, detail="OneSignal is disabled")
    
    # Validate platform
    if request.platform not in ["ios", "android", "web"]:
        raise HTTPException(status_code=400, detail="Platform must be 'ios', 'android', or 'web'")
    
    # Check if player already exists
    existing = db.query(OneSignalPlayer).filter(
        OneSignalPlayer.player_id == request.player_id
    ).first()
    
    if existing:
        # Update user if different, and update last_active
        if existing.user_id != current_user.account_id:
            existing.user_id = current_user.account_id
        existing.last_active = datetime.utcnow()
        existing.is_valid = True  # Re-validate if it was marked invalid
        existing.platform = request.platform
        db.commit()
        logger.info(f"Updated OneSignal player {request.player_id} for user {current_user.account_id}")
        return {
            "message": "Player updated",
            "player_id": request.player_id,
            "user_id": current_user.account_id
        }
    
    # Create new player
    new_player = OneSignalPlayer(
        user_id=current_user.account_id,
        player_id=request.player_id,
        platform=request.platform,
        is_valid=True,
        last_active=datetime.utcnow()
    )
    db.add(new_player)
    db.commit()
    
    logger.info(f"Registered OneSignal player {request.player_id} for user {current_user.account_id}")
    
    return {
        "message": "Player registered",
        "player_id": request.player_id,
        "user_id": current_user.account_id
    }


@router.get("/players")
async def list_players(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List user's registered OneSignal players (for debugging)"""
    if not ONESIGNAL_ENABLED:
        raise HTTPException(status_code=403, detail="OneSignal is disabled")
    
    players = db.query(OneSignalPlayer).filter(
        OneSignalPlayer.user_id == current_user.account_id
    ).all()
    
    return {
        "players": [
            {
                "player_id": p.player_id,
                "platform": p.platform,
                "is_valid": p.is_valid,
                "created_at": p.created_at.isoformat(),
                "last_active": p.last_active.isoformat(),
                "last_failure_at": p.last_failure_at.isoformat() if p.last_failure_at else None
            }
            for p in players
        ]
    }

