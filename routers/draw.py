from fastapi import APIRouter, Depends
from utils.draw_calculations import get_next_draw_time
from models import User
from routers.dependencies import get_current_user

# Create router for draw endpoints
router = APIRouter(prefix="/draw", tags=["Draw"])

@router.get("/next")
def get_draw_time(user: User = Depends(get_current_user) ):
    """
    Endpoint to get the next draw time.
    Returns the next daily draw time (default 8 PM EST).
    If current time is before today's draw time, returns today's draw time.
    If current time is after today's draw time, returns tomorrow's draw time.
    """
    next_draw_time = get_next_draw_time()
    return {"next_draw_time": next_draw_time}
