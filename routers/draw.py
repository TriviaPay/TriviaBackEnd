from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import date, datetime
from typing import List, Dict, Any
from utils.draw_calculations import get_next_draw_time
from models import User
from routers.dependencies import get_current_user
from db import get_db
from rewards_logic import calculate_prize_pool, get_eligible_participants

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

@router.get("/eligible-participants")
def get_eligible_participants_for_draw(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all users who are currently eligible for the next draw.
    
    Eligibility criteria:
    - User must be subscribed for the current month (subscription_flag == True)
    - User must have answered today's questions correctly (daily_eligibility_flag == True)
    
    Returns:
        List of eligible participants with their profile pictures and names for the next draw
    """
    try:
        # Get the next draw time for context
        next_draw_time = get_next_draw_time()
        
        # Get eligible participants using the existing logic (current state)
        participants = get_eligible_participants(db, date.today())
        
        if not participants:
            return {
                "next_draw_time": next_draw_time.isoformat(),
                "total_participants": 0,
                "participants": []
            }
        
        # Get full user details for the eligible participants
        account_ids = [p["account_id"] for p in participants]
        eligible_users = db.query(User).filter(User.account_id.in_(account_ids)).all()
        
        # Format the response with profile pictures and names
        participants_data = []
        for user in eligible_users:
            participants_data.append({
                "account_id": user.account_id,
                "username": user.username,
                "display_name": user.display_name or user.username,
                "profile_pic_url": user.profile_pic_url,
                "is_admin": user.is_admin,
                "badge_id": user.badge_id,
                "is_winner": user.badge_id in ["gold", "silver", "bronze"]
            })
        
        return {
            "next_draw_time": next_draw_time.isoformat(),
            "total_participants": len(participants_data),
            "participants": participants_data
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving eligible participants: {str(e)}"
        )
