from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import date
from utils.draw_calculations import get_next_draw_time
from models import User
from routers.dependencies import get_current_user
from db import get_db
from rewards_logic import calculate_prize_pool

# Create router for draw endpoints
router = APIRouter(prefix="/draw", tags=["Draw"])

@router.get("/next")
def get_draw_time(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Endpoint to get the next draw time and current prize pool.
    Returns the next daily draw time (default 8 PM EST) and the prize pool for today's draw.
    If current time is before today's draw time, returns today's draw time.
    If current time is after today's draw time, returns tomorrow's draw time.
    """
    next_draw_time = get_next_draw_time()
    
    # Calculate prize pool for today's draw
    today = date.today()
    prize_pool = calculate_prize_pool(db, today)
    
    return {
        "next_draw_time": next_draw_time,
        "prize_pool": prize_pool
    }
