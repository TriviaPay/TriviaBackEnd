import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from db import get_db
from models import User

# TriviaQuestionsDaily, Trivia, TriviaQuestionsEntries, TriviaUserDaily removed - legacy tables
from routers.dependencies import get_current_user

router = APIRouter(tags=["Rewards"])

from .service import get_daily_login_status as service_get_daily_login_status
from .service import get_recent_winners as service_get_recent_winners
from .service import process_daily_login as service_process_daily_login


@router.get("/recent-winners")
async def get_recent_winners(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """
    Get recent winners from bronze and silver modes.
    Returns top 10 winners from each mode (max 20 total) for the most recent completed draw.
    """
    try:
        return service_get_recent_winners(db, current_user)
    except Exception as e:
        logging.error(f"Error getting recent winners: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Error retrieving recent winners: {str(e)}"
        )


# =================================
# Daily Login Rewards Endpoints
# =================================


@router.get("/daily-login")
async def get_daily_login_status(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Get current week's daily login status"""
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return service_get_daily_login_status(db, user)


@router.post("/daily-login")
async def process_daily_login(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Process daily login rewards - weekly calendar system"""
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return service_process_daily_login(db, user)
