from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Dict, Any
from utils.draw_calculations import get_next_draw_time
from utils.trivia_mode_service import get_today_in_app_timezone
from models import User
from routers.dependencies import get_current_user
from db import get_db
from rewards_logic import calculate_prize_pool
from config import DRAW_PRIZE_POOL_CACHE_SECONDS
# Legacy get_eligible_participants removed - TriviaUserDaily table deleted

# Create router for draw endpoints
router = APIRouter(prefix="/draw", tags=["Draw"])

_PRIZE_POOL_CACHE = {
    "date": None,
    "value": None,
    "expires_at": None,
}
_PRIZE_POOL_TTL_SECONDS = DRAW_PRIZE_POOL_CACHE_SECONDS

@router.get("/next")
def get_draw_time(
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Endpoint to get the next draw time and current prize pool.
    Returns the next daily draw time (default 8 PM EST) and the prize pool for today's draw.
    If current time is before today's draw time, returns today's draw time.
    If current time is after today's draw time, returns tomorrow's draw time.
    """
    next_draw_time = get_next_draw_time()
    
    # Calculate prize pool using app timezone date, with short-lived cache
    today = get_today_in_app_timezone()
    now = datetime.utcnow()
    cached_date = _PRIZE_POOL_CACHE.get("date")
    cached_expires = _PRIZE_POOL_CACHE.get("expires_at")
    if cached_date == today and cached_expires and cached_expires > now:
        prize_pool = _PRIZE_POOL_CACHE.get("value")
    else:
        prize_pool = calculate_prize_pool(db, today, commit_revenue=False)
        _PRIZE_POOL_CACHE["date"] = today
        _PRIZE_POOL_CACHE["value"] = prize_pool
        _PRIZE_POOL_CACHE["expires_at"] = now + timedelta(seconds=_PRIZE_POOL_TTL_SECONDS)
    
    return {
        "next_draw_time": next_draw_time.isoformat(),
        "prize_pool": prize_pool
    }

# Legacy /eligible-participants endpoint removed - TriviaUserDaily table deleted
# Use mode-specific eligibility endpoints instead
