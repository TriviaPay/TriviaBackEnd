from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime
from collections import OrderedDict, deque
import threading
import time

from db import get_db
from models import User, OneSignalPlayer
from routers.dependencies import get_current_user
from config import ONESIGNAL_ENABLED, ONESIGNAL_MAX_PLAYERS_PER_USER
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/onesignal", tags=["OneSignal"])

_rate_limit_store = OrderedDict()
_rate_limit_lock = threading.Lock()
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 20
RATE_LIMIT_MAX_KEYS = 10000


def _check_rate_limit(identifier: str) -> bool:
    now = time.time()
    with _rate_limit_lock:
        bucket = _rate_limit_store.get(identifier)
        if bucket is None:
            bucket = deque()
            _rate_limit_store[identifier] = bucket
        else:
            _rate_limit_store.move_to_end(identifier)

        while bucket and now - bucket[0] >= RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            return False

        bucket.append(now)
        if len(_rate_limit_store) > RATE_LIMIT_MAX_KEYS:
            _rate_limit_store.popitem(last=False)

    return True


class RegisterPlayerRequest(BaseModel):
    player_id: str = Field(..., description="OneSignal player ID")
    platform: str = Field(..., description="Platform: 'ios', 'android', or 'web'")


@router.post("/register")
def register_player(
    request: RegisterPlayerRequest,
    req: Request,
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
        # Prevent reassignment to a different user
        if existing.user_id != current_user.account_id:
            raise HTTPException(status_code=409, detail="Player ID is already registered to another user")
        existing.last_active = datetime.utcnow()
        existing.is_valid = True  # Re-validate if it was marked invalid
        existing.platform = request.platform
        try:
            db.commit()
            logger.info(f"Updated OneSignal player {request.player_id} for user {current_user.account_id}")
            return {
                "message": "Player updated",
                "player_id": request.player_id,
                "user_id": current_user.account_id
            }
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to update OneSignal player {request.player_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to update player")

    player_count = db.query(func.count(OneSignalPlayer.id)).filter(
        OneSignalPlayer.user_id == current_user.account_id
    ).scalar() or 0
    if player_count >= ONESIGNAL_MAX_PLAYERS_PER_USER:
        raise HTTPException(status_code=409, detail="Player limit reached for this user")
    
    # Create new player
    new_player = OneSignalPlayer(
        user_id=current_user.account_id,
        player_id=request.player_id,
        platform=request.platform,
        is_valid=True,
        last_active=datetime.utcnow()
    )
    db.add(new_player)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to register OneSignal player {request.player_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to register player")
    
    logger.info(f"Registered OneSignal player {request.player_id} for user {current_user.account_id}")
    
    return {
        "message": "Player registered",
        "player_id": request.player_id,
        "user_id": current_user.account_id
    }


@router.get("/players")
def list_players(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List user's registered OneSignal players (for debugging)"""
    if not ONESIGNAL_ENABLED:
        raise HTTPException(status_code=403, detail="OneSignal is disabled")
    
    players = db.query(OneSignalPlayer).filter(
        OneSignalPlayer.user_id == current_user.account_id
    ).order_by(
        desc(OneSignalPlayer.created_at)
    ).offset(offset).limit(limit).all()
    
    total = db.query(func.count(OneSignalPlayer.id)).filter(
        OneSignalPlayer.user_id == current_user.account_id
    ).scalar() or 0
    
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
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
    ip = req.client.host if req.client else "unknown"
    rl_key = f"osreg:{ip}:{current_user.account_id}"
    if not _check_rate_limit(rl_key):
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.")
