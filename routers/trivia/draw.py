from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db import get_db
from models import User
from routers.dependencies import get_current_user

from .service import get_next_draw_with_prize_pool

# Legacy get_eligible_participants removed - TriviaUserDaily table deleted

# Create router for draw endpoints
router = APIRouter(prefix="/draw", tags=["Draw"])


@router.get("/next")
def get_draw_time(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Endpoint to get the next draw time and current prize pool.
    Returns the next daily draw time (default 8 PM EST) and the prize pool for today's draw.
    If current time is before today's draw time, returns today's draw time.
    If current time is after today's draw time, returns tomorrow's draw time.
    """
    return get_next_draw_with_prize_pool(db)


# Legacy /eligible-participants endpoint removed - TriviaUserDaily table deleted
# Use mode-specific eligibility endpoints instead
