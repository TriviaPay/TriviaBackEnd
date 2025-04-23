from datetime import date, datetime
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from db import get_db
from models import Winner, User, TriviaDrawWinner, DrawConfig
from routers.dependencies import get_current_user
from rewards_logic import get_daily_winners, get_weekly_winners, get_all_time_winners

router = APIRouter(prefix="/winners", tags=["Winners"])

@router.get("/")
def get_recent_winners(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)  # Protect this route
):
    """
    Endpoint to fetch recent winners. Only accessible if you have a valid Auth0 token.
    Fetches up to 5 most recent winners from the database.
    """
    winners = (
        db.query(Winner)
          .order_by(Winner.win_date.desc())
          .limit(5)
          .all()
    )

    return {
        "winners": [
            {
                "account_id": w.account_id,
                "amount_won": w.amount_won,
                "win_date": w.win_date,
                "profile_pic_url": w.user.profile_pic_url if w.user else None,
            }
            for w in winners
        ]
    }

@router.get("/daily-winners", response_model=List[Dict[str, Any]])
async def get_daily_winner_list(
    specific_date: Optional[date] = None,
    db: Session = Depends(get_db)
):
    """
    Get the list of daily winners.
    If specific_date is provided, returns winners for that day.
    Otherwise, returns winners for the most recent draw.
    
    Returns:
        List of winners with:
        - User info (username, badge, avatar, frame)
        - Position in the draw
        - Amount won in the draw
        - Total amount won all-time
    """
    winners = get_daily_winners(db, specific_date)
    return winners

@router.get("/weekly-winners", response_model=List[Dict[str, Any]])
async def get_weekly_winner_list(
    db: Session = Depends(get_db)
):
    """
    Get the list of winners for the past week, sorted by total amount won in the week.
    
    Returns:
        List of winners with:
        - User info (username, badge, avatar, frame)
        - Amount won in the past week
        - Total amount won all-time
    """
    winners = get_weekly_winners(db)
    return winners

@router.get("/all-time-winners", response_model=List[Dict[str, Any]])
async def get_all_time_winner_list(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db)
):
    """
    Get the list of all-time winners, sorted by total amount won.
    
    Args:
        limit: Maximum number of winners to return (default: 10, max: 50)
        
    Returns:
        List of winners with:
        - User info (username, badge, avatar, frame)
        - Total amount won all-time
    """
    winners = get_all_time_winners(db, limit=limit)
    return winners
