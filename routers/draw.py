from fastapi import APIRouter
from utils import get_next_draw_time

# Create router for draw endpoints
router = APIRouter(prefix="/draw", tags=["Draw"])

@router.get("/next")
def get_draw_time():
    """
    Endpoint to get the next draw time.
    Returns the last day of the current month at 8 PM EST.
    """
    next_draw_time = get_next_draw_time()
    return {"next_draw_time": next_draw_time}
