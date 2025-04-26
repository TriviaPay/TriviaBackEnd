import os
import random
import logging
import json
from datetime import datetime, timedelta, date
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_
import pytz

from models import User, TriviaDrawWinner, DrawConfig, Entry, Badge, Avatar, Frame
from db import get_db

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_draw_config(db: Session) -> DrawConfig:
    """
    Get the current draw configuration. Create a default one if it doesn't exist.
    """
    config = db.query(DrawConfig).first()
    if not config:
        config = DrawConfig(is_custom=False, custom_winner_count=None)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config

def get_draw_time() -> Dict[str, Any]:
    """
    Get the current draw time settings from environment variables.
    """
    draw_time = {
        "hour": int(os.environ.get("DRAW_TIME_HOUR", "20")),
        "minute": int(os.environ.get("DRAW_TIME_MINUTE", "0")),
        "timezone": os.environ.get("DRAW_TIMEZONE", "US/Eastern")
    }
    return draw_time

def is_draw_time(current_time: datetime = None) -> bool:
    """
    Check if it's time to run the draw.
    """
    if not current_time:
        current_time = datetime.now(pytz.UTC)
    
    draw_settings = get_draw_time()
    tz = pytz.timezone(draw_settings["timezone"])
    current_time_in_tz = current_time.astimezone(tz)
    
    return (current_time_in_tz.hour == draw_settings["hour"] and 
            current_time_in_tz.minute == draw_settings["minute"])

def get_eligible_participants(db: Session, draw_date: date) -> List[Dict[str, Any]]:
    """
    Get all eligible participants for the daily draw.
    
    Eligibility criteria:
    - The user must have participated in at least one trivia on the draw date
    - The user must have answered correctly
    """
    start_date = datetime.combine(draw_date, datetime.min.time())
    end_date = datetime.combine(draw_date, datetime.max.time())
    
    # Query users who have correct entries on the specified date
    eligible_users = db.query(User).join(Entry).filter(
        Entry.created_at.between(start_date, end_date),
        Entry.is_correct == True
    ).distinct().all()
    
    return [
        {
            "account_id": user.account_id,
            "username": user.username
        }
        for user in eligible_users
    ]

def perform_draw(db: Session, draw_date: date) -> Dict[str, Any]:
    """
    Perform the daily draw for the specified date.
    
    Args:
        db: Database session
        draw_date: The date to perform the draw for
        
    Returns:
        Dict containing draw results
    """
    logger.info(f"Performing draw for date: {draw_date}")
    
    # Check if a draw has already been performed for this date
    existing_draw = db.query(TriviaDrawWinner).filter(
        TriviaDrawWinner.draw_date == draw_date
    ).first()
    
    if existing_draw:
        logger.info(f"Draw for {draw_date} has already been performed")
        return {
            "status": "already_performed",
            "draw_date": draw_date,
            "message": f"Draw for {draw_date} has already been performed"
        }
    
    # Get eligible participants
    participants = get_eligible_participants(db, draw_date)
    participant_count = len(participants)
    
    if participant_count == 0:
        logger.info(f"No eligible participants for draw on {draw_date}")
        return {
            "status": "no_participants",
            "draw_date": draw_date,
            "message": f"No eligible participants for draw on {draw_date}"
        }
    
    # Calculate number of winners based on config
    config = get_draw_config(db)
    
    # Determine number of winners
    winner_count = 0
    if config.is_custom and config.custom_winner_count:
        winner_count = min(config.custom_winner_count, participant_count)
    else:
        # Default logic: 10% of participants, minimum 1, maximum 10
        winner_count = max(1, min(10, int(participant_count * 0.1)))
    
    logger.info(f"Number of winners: {winner_count}, Eligible participants: {participant_count}")
    
    # Shuffle participants and select winners
    random.shuffle(participants)
    selected_winners = participants[:winner_count]
    
    # Calculate prize amount (equal distribution)
    prize_pool = 100.0  # Default prize pool of $100
    individual_prize = prize_pool / winner_count
    
    # Save winners to database
    winners = []
    for position, winner in enumerate(selected_winners, 1):
        winner_record = TriviaDrawWinner(
            account_id=winner["account_id"],
            prize_amount=individual_prize,
            position=position,
            draw_date=draw_date
        )
        db.add(winner_record)
        winners.append({
            "account_id": winner["account_id"],
            "username": winner["username"],
            "position": position,
            "prize_amount": individual_prize
        })
    
    db.commit()
    
    return {
        "status": "success",
        "draw_date": draw_date,
        "total_participants": participant_count,
        "total_winners": winner_count,
        "prize_pool": prize_pool,
        "winners": winners
    }

def get_user_details(user, db: Session) -> Dict[str, Any]:
    """
    Extract user details for display with cosmetic items (badge, avatar, frame).
    """
    # Get badge, avatar, and frame info
    badge_info = db.query(Badge).filter(Badge.id == user.badge_id).first() if user.badge_id else None
    
    # Use selected_avatar_id instead of avatar_id (which doesn't exist in User model)
    avatar_id = getattr(user, 'selected_avatar_id', None)
    avatar_info = db.query(Avatar).filter(Avatar.id == avatar_id).first() if avatar_id else None
    
    # Use selected_frame_id instead of frame_id
    frame_id = getattr(user, 'selected_frame_id', None)
    frame_info = db.query(Frame).filter(Frame.id == frame_id).first() if frame_id else None
    
    return {
        "account_id": user.account_id,
        "username": user.username,
        "badge_image_url": badge_info.image_url if badge_info else None,
        "avatar_image_url": avatar_info.image_url if avatar_info else None,
        "frame_image_url": frame_info.image_url if frame_info else None
    }

def get_daily_winners(db: Session, specific_date: Optional[date] = None) -> List[Dict[str, Any]]:
    """
    Get the winners for a specific day.
    If no date is provided, returns winners for the most recent draw.
    
    Returns:
        List of winners with details including username, badge, frame, avatar, 
        amount won that day, and total amount won all-time.
    """
    query = db.query(TriviaDrawWinner).join(User)
    
    if specific_date:
        # Filter by specific date
        query = query.filter(TriviaDrawWinner.draw_date == specific_date)
    else:
        # Get most recent draw date
        most_recent_date = db.query(func.max(TriviaDrawWinner.draw_date)).scalar()
        if not most_recent_date:
            return []
        query = query.filter(TriviaDrawWinner.draw_date == most_recent_date)
    
    # Order by position
    winners = query.order_by(TriviaDrawWinner.position).all()
    
    result = []
    for winner in winners:
        user = winner.user
        
        # Get total amount won by this user all-time
        total_amount = db.query(func.sum(TriviaDrawWinner.prize_amount)).filter(
            TriviaDrawWinner.account_id == user.account_id
        ).scalar() or 0.0
        
        # Get user details
        user_details = get_user_details(user, db)
        
        result.append({
            **user_details,
            "position": winner.position,
            "amount_won": winner.prize_amount,
            "total_amount_won": total_amount,
            "draw_date": winner.draw_date.isoformat()
        })
    
    return result

def get_weekly_winners(db: Session) -> List[Dict[str, Any]]:
    """
    Get the winners for the past week, sorted by total amount won in the week.
    
    Returns:
        List of winners with details including username, badge, frame, avatar,
        amount won this week, and total amount won all-time.
    """
    # Calculate start of the week (7 days ago)
    end_date = date.today()
    start_date = end_date - timedelta(days=7)
    
    # Get all winners from the past week
    winners_past_week = db.query(
        User,
        func.sum(TriviaDrawWinner.prize_amount).label("weekly_amount")
    ).join(
        TriviaDrawWinner, User.account_id == TriviaDrawWinner.account_id
    ).filter(
        TriviaDrawWinner.draw_date.between(start_date, end_date)
    ).group_by(User.account_id).order_by(desc("weekly_amount")).limit(10).all()
    
    result = []
    for user, weekly_amount in winners_past_week:
        # Get total amount won by this user all-time
        total_amount = db.query(func.sum(TriviaDrawWinner.prize_amount)).filter(
            TriviaDrawWinner.account_id == user.account_id
        ).scalar() or 0.0
        
        # Get user details
        user_details = get_user_details(user, db)
        
        result.append({
            **user_details,
            "weekly_amount": weekly_amount,
            "total_amount_won": total_amount
        })
    
    return result

def get_all_time_winners(db: Session, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Get the top winners of all time, sorted by total amount won.
    
    Args:
        db: Database session
        limit: Maximum number of winners to return
        
    Returns:
        List of winners with details including username, badge, frame, avatar,
        and total amount won all-time.
    """
    # Get all-time winners
    all_time_winners = db.query(
        User,
        func.sum(TriviaDrawWinner.prize_amount).label("total_amount")
    ).join(
        TriviaDrawWinner, User.account_id == TriviaDrawWinner.account_id
    ).group_by(User.account_id).order_by(desc("total_amount")).limit(limit).all()
    
    result = []
    for user, total_amount in all_time_winners:
        # Get user details
        user_details = get_user_details(user, db)
        
        result.append({
            **user_details,
            "total_amount_won": total_amount
        })
    
    return result

def get_all_day_wise_winners(db: Session, days_limit: int = 30) -> List[Dict[str, List[Dict[str, Any]]]]:
    """
    Get winners organized by day, with the most recent days first.
    
    Args:
        db: Database session
        days_limit: Maximum number of days to return
        
    Returns:
        List of day entries, each containing:
        - draw_date: The date of the draw
        - winners: List of winners for that day with their details
          (username, avatar, frame, badge, amount won)
    """
    # Get the distinct draw dates ordered by most recent first
    distinct_dates = db.query(TriviaDrawWinner.draw_date)\
        .distinct()\
        .order_by(TriviaDrawWinner.draw_date.desc())\
        .limit(days_limit)\
        .all()
    
    result = []
    for (draw_date,) in distinct_dates:
        # Get winners for this specific date
        day_winners = get_daily_winners(db, draw_date)
        
        # Add to result with the date
        result.append({
            "draw_date": draw_date.isoformat(),
            "winners": day_winners
        })
    
    return result

def get_top_recent_winners(db: Session, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Get the top recent winners regardless of which day they won.
    
    This function returns the most recent winners based on draw date,
    limited to the specified number, regardless of which days they won on.
    
    Args:
        db: Database session
        limit: Maximum number of winners to return
        
    Returns:
        List of winners with details including username, badge, avatar, frame,
        amount won, position, and the date they won.
    """
    # Get the most recent winners ordered by draw date (newest first)
    recent_winners = db.query(TriviaDrawWinner).join(User)\
        .order_by(TriviaDrawWinner.draw_date.desc(), TriviaDrawWinner.position)\
        .limit(limit)\
        .all()
    
    result = []
    for winner in recent_winners:
        user = winner.user
        
        # Get total amount won by this user all-time
        total_amount = db.query(func.sum(TriviaDrawWinner.prize_amount)).filter(
            TriviaDrawWinner.account_id == user.account_id
        ).scalar() or 0.0
        
        # Get user details
        user_details = get_user_details(user, db)
        
        result.append({
            **user_details,
            "position": winner.position,
            "amount_won": winner.prize_amount,
            "total_amount_won": total_amount,
            "draw_date": winner.draw_date.isoformat()
        })
    
    return result 