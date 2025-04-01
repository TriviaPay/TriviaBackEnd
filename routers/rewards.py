from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime, timedelta

from db import get_db
from models import User, Winner
from auth import verify_access_token
from routers.dependencies import get_current_user

router = APIRouter(prefix="/rewards", tags=["Rewards"])

@router.get("/pool")
async def get_rewards_pool(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current rewards pool information"""
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token")

    # TODO: Implement actual rewards pool logic
    # For now, return static data
    return {
        "total_pool": 10000,
        "current_entries": 500,
        "time_remaining": "2h 30m",
        "prize_tiers": [
            {"position": 1, "amount": 5000},
            {"position": 2, "amount": 2500},
            {"position": 3, "amount": 1000},
            {"position": "4-10", "amount": 200},
        ]
    }

@router.get("/winners/today")
async def get_todays_winners(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get today's winners"""
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token")

    today = datetime.utcnow().date()
    winners = db.query(Winner, User).join(User).filter(
        func.date(Winner.win_date) == today
    ).order_by(Winner.amount_won.desc()).all()

    return {
        "winners": [
            {
                "position": i + 1,
                "name": f"{w.User.first_name} {w.User.last_name}" if w.User.first_name else "Anonymous",
                "amount": w.Winner.amount_won,
                "profile_pic": w.User.profile_pic_url
            }
            for i, w in enumerate(winners)
        ]
    }

@router.get("/winners/all-time")
async def get_all_time_winners(
    claims: dict = Depends(get_current_user),
    page: int = 1,
    limit: int = 10,
    db: Session = Depends(get_db)
):
    """Get all-time winners with pagination"""
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid token")

    offset = (page - 1) * limit
    winners = db.query(Winner, User).join(User).order_by(
        Winner.amount_won.desc()
    ).offset(offset).limit(limit).all()

    total = db.query(Winner).count()

    return {
        "winners": [
            {
                "position": offset + i + 1,
                "name": f"{w.User.first_name} {w.User.last_name}" if w.User.first_name else "Anonymous",
                "amount": w.Winner.amount_won,
                "profile_pic": w.User.profile_pic_url,
                "win_date": w.Winner.win_date.strftime("%Y-%m-%d")
            }
            for i, w in enumerate(winners)
        ],
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit
    }

@router.get("/streaks-gems")
async def get_streaks_and_gems(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user's current streaks and gems"""
    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "streaks": user.streaks,
        "gems": user.gems
    }

@router.post("/streaks-gems")
async def update_streaks_and_gems(
    claims: dict = Depends(get_current_user),
    streaks: Optional[int] = None,
    gems: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """Update user's streaks and/or gems"""
    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if streaks is not None:
        user.streaks = streaks
    if gems is not None:
        user.gems = gems

    db.commit()

    return {
        "streaks": user.streaks,
        "gems": user.gems
    } 