from fastapi import APIRouter, Depends, HTTPException, status, Body, Path, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, text, desc
from datetime import datetime, timedelta, date
import calendar
import pytz
import random
import math
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

from db import get_db
from models import (
    User, CompanyRevenue, TriviaModeConfig, Avatar, Frame,
    UserSubscription, TriviaBronzeModeLeaderboard, TriviaSilverModeLeaderboard,
    UserDailyRewards
)
# TriviaQuestionsDaily, Trivia, TriviaQuestionsEntries, TriviaUserDaily removed - legacy tables
from routers.dependencies import get_current_user, get_admin_user
from utils.trivia_mode_service import get_active_draw_date, get_today_in_app_timezone
from utils.storage import presign_get
from sqlalchemy.sql import extract
import os
import json
import logging

router = APIRouter(tags=["Rewards"])

# ======== Helper Functions ========

def round_down(value: float, decimals: int = 2) -> float:
    """Round down to specified number of decimal places (lower limit)."""
    multiplier = 10 ** decimals
    return math.floor(value * multiplier) / multiplier

# ======== Models ========
# (No models needed for remaining endpoints)

# Import unified functions from rewards_logic
from rewards_logic import (
    calculate_winner_count, 
    calculate_prize_distribution, 
    calculate_prize_pool,
    reset_daily_eligibility_flags,
    reset_monthly_subscriptions
)
# Legacy functions removed: get_eligible_participants, update_user_eligibility (TriviaUserDaily table deleted)

# ======== Helper Functions ========

# All helper functions are now imported from rewards_logic.py for consistency

# ======== API Endpoints ========

@router.get("/recent-winners")
async def get_recent_winners(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get recent winners from bronze and silver modes.
    Returns top 10 winners from each mode (max 20 total) for the most recent completed draw.
    """
    try:
        # Get the most recent draw_date that has winners in either bronze or silver mode
        from sqlalchemy import func
        
        # Find the most recent draw_date from bronze leaderboard
        bronze_max_date = db.query(func.max(TriviaBronzeModeLeaderboard.draw_date)).scalar()
        
        # Find the most recent draw_date from silver leaderboard
        silver_max_date = db.query(func.max(TriviaSilverModeLeaderboard.draw_date)).scalar()
        
        # Use the most recent date between the two
        if bronze_max_date and silver_max_date:
            draw_date = max(bronze_max_date, silver_max_date)
        elif bronze_max_date:
            draw_date = bronze_max_date
        elif silver_max_date:
            draw_date = silver_max_date
        else:
            # Fallback to calculated date if no winners exist
            active_date = get_active_draw_date()
            today = get_today_in_app_timezone()
            if active_date == today:
                draw_date = active_date
            else:
                draw_date = active_date
        
        # Get top 10 bronze mode winners from leaderboard
        bronze_winners = db.query(TriviaBronzeModeLeaderboard).filter(
            TriviaBronzeModeLeaderboard.draw_date == draw_date
        ).order_by(TriviaBronzeModeLeaderboard.position).limit(10).all()
        
        # Get top 10 silver mode winners from leaderboard
        silver_winners = db.query(TriviaSilverModeLeaderboard).filter(
            TriviaSilverModeLeaderboard.draw_date == draw_date
        ).order_by(TriviaSilverModeLeaderboard.position).limit(10).all()
        
        # Get all unique user IDs
        all_user_ids = set()
        for winner in bronze_winners:
            all_user_ids.add(winner.account_id)
        for winner in silver_winners:
            all_user_ids.add(winner.account_id)
        
        # Batch load users
        users = {u.account_id: u for u in db.query(User).filter(User.account_id.in_(list(all_user_ids))).all()}
        
        # Get profile data for all users
        from utils.chat_helpers import get_user_chat_profile_data_bulk
        
        profile_map = get_user_chat_profile_data_bulk(list(users.values()), db)
        
        result = []
        
        # Process bronze winners
        for winner in bronze_winners:
            user = users.get(winner.account_id)
            if not user:
                continue
            
            profile_data = profile_map.get(winner.account_id, {})
            badge_data = profile_data.get('badge') or {}
            
            result.append({
                'mode': 'bronze',
                'position': winner.position,
                'username': user.username,
                'user_id': winner.account_id,
                'money_awarded': round_down(float(winner.money_awarded), 2),
                'submitted_at': winner.submitted_at.isoformat() if winner.submitted_at else None,
                'profile_pic': profile_data.get('profile_pic_url'),
                'badge_image_url': badge_data.get('image_url'),
                'avatar_url': profile_data.get('avatar_url'),
                'frame_url': profile_data.get('frame_url'),
                'subscription_badges': profile_data.get('subscription_badges', []),
                'level': profile_data.get('level', 1),
                'level_progress': profile_data.get('level_progress', '0/100'),
                'draw_date': draw_date.isoformat()
            })
        
        # Process silver winners
        for winner in silver_winners:
            user = users.get(winner.account_id)
            if not user:
                continue
            
            profile_data = profile_map.get(winner.account_id, {})
            badge_data = profile_data.get('badge') or {}
            
            result.append({
                'mode': 'silver',
                'position': winner.position,
                'username': user.username,
                'user_id': winner.account_id,
                'money_awarded': round_down(float(winner.money_awarded), 2),
                'submitted_at': winner.submitted_at.isoformat() if winner.submitted_at else None,
                'profile_pic': profile_data.get('profile_pic_url'),
                'badge_image_url': badge_data.get('image_url'),
                'avatar_url': profile_data.get('avatar_url'),
                'frame_url': profile_data.get('frame_url'),
                'subscription_badges': profile_data.get('subscription_badges', []),
                'level': profile_data.get('level', 1),
                'level_progress': profile_data.get('level_progress', '0/100'),
                'draw_date': draw_date.isoformat()
            })
        
        return {
            'draw_date': draw_date.isoformat(),
            'total_winners': len(result),
            'bronze_winners': len([w for w in result if w['mode'] == 'bronze']),
            'silver_winners': len([w for w in result if w['mode'] == 'silver']),
            'winners': result
        }
    
    except Exception as e:
        logging.error(f"Error getting recent winners: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving recent winners: {str(e)}"
        )


# =================================
# Daily Login Rewards Endpoints
# =================================

@router.get("/daily-login")
async def get_daily_login_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current week's daily login status"""
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    today = get_today_in_app_timezone()
    # Calculate week start (Monday)
    week_start = today - timedelta(days=today.weekday())
    
    # Get or create user's weekly rewards record
    user_rewards = db.query(UserDailyRewards).filter(
        UserDailyRewards.account_id == user.account_id,
        UserDailyRewards.week_start_date == week_start
    ).first()
    
    if not user_rewards:
        # No record means no days claimed yet this week
        days_claimed = []
        total_gems_earned = 0
    else:
        # Build list of claimed days (1-7 for Mon-Sun)
        days_claimed = []
        if user_rewards.day1_status: days_claimed.append(1)
        if user_rewards.day2_status: days_claimed.append(2)
        if user_rewards.day3_status: days_claimed.append(3)
        if user_rewards.day4_status: days_claimed.append(4)
        if user_rewards.day5_status: days_claimed.append(5)
        if user_rewards.day6_status: days_claimed.append(6)
        if user_rewards.day7_status: days_claimed.append(7)
        
        # Calculate total gems earned (10 per day, 30 for Sunday)
        total_gems_earned = len([d for d in days_claimed if d != 7]) * 10
        if 7 in days_claimed:
            total_gems_earned += 30
    
    # Current day of week (0=Monday, 6=Sunday, convert to 1-7)
    current_day = today.weekday() + 1
    
    # Days remaining in week
    days_remaining = 7 - len(days_claimed)
    
    return {
        "week_start_date": week_start.isoformat(),
        "current_day": current_day,
        "days_claimed": days_claimed,
        "days_remaining": days_remaining,
        "total_gems_earned_this_week": total_gems_earned,
        "day_status": {
            "monday": user_rewards.day1_status if user_rewards else False,
            "tuesday": user_rewards.day2_status if user_rewards else False,
            "wednesday": user_rewards.day3_status if user_rewards else False,
            "thursday": user_rewards.day4_status if user_rewards else False,
            "friday": user_rewards.day5_status if user_rewards else False,
            "saturday": user_rewards.day6_status if user_rewards else False,
            "sunday": user_rewards.day7_status if user_rewards else False,
        }
    }

@router.post("/daily-login")
async def process_daily_login(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Process daily login rewards - weekly calendar system"""
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    today = get_today_in_app_timezone()
    # Calculate week start (Monday)
    week_start = today - timedelta(days=today.weekday())
    
    # Get or create user's weekly rewards record
    user_rewards = db.query(UserDailyRewards).filter(
        UserDailyRewards.account_id == user.account_id,
        UserDailyRewards.week_start_date == week_start
    ).first()
    
    if not user_rewards:
        user_rewards = UserDailyRewards(
            account_id=user.account_id,
            week_start_date=week_start
        )
        db.add(user_rewards)
    
    # Determine which day of week (1=Monday, 7=Sunday)
    day_of_week = today.weekday() + 1
    
    # Check if already claimed today
    day_status_map = {
        1: user_rewards.day1_status,
        2: user_rewards.day2_status,
        3: user_rewards.day3_status,
        4: user_rewards.day4_status,
        5: user_rewards.day5_status,
        6: user_rewards.day6_status,
        7: user_rewards.day7_status,
    }
    
    if day_status_map[day_of_week]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Daily reward already claimed today"
        )
    
    # Award gems: 10 for Mon-Sat, 30 for Sunday
    gems_earned = 30 if day_of_week == 7 else 10
    user.gems += gems_earned
    
    # Mark the day as claimed
    if day_of_week == 1:
        user_rewards.day1_status = True
    elif day_of_week == 2:
        user_rewards.day2_status = True
    elif day_of_week == 3:
        user_rewards.day3_status = True
    elif day_of_week == 4:
        user_rewards.day4_status = True
    elif day_of_week == 5:
        user_rewards.day5_status = True
    elif day_of_week == 6:
        user_rewards.day6_status = True
    elif day_of_week == 7:
        user_rewards.day7_status = True
    
    user_rewards.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user_rewards)
    
    # Calculate days claimed for response
    days_claimed = []
    if user_rewards.day1_status: days_claimed.append(1)
    if user_rewards.day2_status: days_claimed.append(2)
    if user_rewards.day3_status: days_claimed.append(3)
    if user_rewards.day4_status: days_claimed.append(4)
    if user_rewards.day5_status: days_claimed.append(5)
    if user_rewards.day6_status: days_claimed.append(6)
    if user_rewards.day7_status: days_claimed.append(7)
    
    return {
        "success": True,
        "gems_earned": gems_earned,
        "total_gems": user.gems,
        "week_start_date": week_start.isoformat(),
        "current_day": day_of_week,
        "days_claimed": days_claimed,
        "days_remaining": 7 - len(days_claimed)
    }
