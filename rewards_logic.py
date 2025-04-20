import os
import random
import logging
import json
from datetime import datetime, timedelta, date, time
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_
import pytz
from fastapi import HTTPException

from models import User, TriviaDrawWinner, TriviaDrawConfig, Entry, Badge, Avatar, Frame, DailyQuestion, UserQuestionAnswer
from db import get_db

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_draw_config(db: Session) -> TriviaDrawConfig:
    """
    Get the current draw configuration. Create a default one if it doesn't exist.
    """
    config = db.query(TriviaDrawConfig).order_by(TriviaDrawConfig.id.desc()).first()
    if not config:
        config = TriviaDrawConfig(
            is_custom=False, 
            custom_winner_count=None,
            daily_pool_amount=0.0,
            daily_winners_count=1,
            automatic_draws=True,
            draw_time_hour=int(os.environ.get("DRAW_TIME_HOUR", "20")),
            draw_time_minute=int(os.environ.get("DRAW_TIME_MINUTE", "0")),
            draw_timezone=os.environ.get("DRAW_TIMEZONE", "US/Eastern"),
            use_dynamic_calculation=True
        )
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

def get_eligible_participants(db: Session, draw_date: date) -> List[User]:
    """
    Get eligible participants for the draw on the specified date.
    
    Eligibility criteria:
    1. Users who answered correctly at least one daily question for the specified date
    2. If no users found, check users who entered trivia on that date
    3. If still no users, return test users as fallback
    
    Args:
        db: Database session
        draw_date: The date to get eligible participants for
        
    Returns:
        List of eligible User objects
    """
    logging.info(f"Getting eligible participants for date: {draw_date}")
    
    # Method 1: Find users who answered daily questions correctly
    eligible_users_query = (
        db.query(User)
        .join(UserQuestionAnswer, User.account_id == UserQuestionAnswer.account_id)
        .filter(
            UserQuestionAnswer.date == draw_date,
            UserQuestionAnswer.is_correct == True
        )
        .distinct()
    )
    
    eligible_users = eligible_users_query.all()
    logging.info(f"Method 1: Found {len(eligible_users)} users who answered daily questions correctly")
    
    # If no eligible users found, try method 2: users who entered trivia that day
    if not eligible_users:
        logging.info("No users found who answered correctly, checking for any trivia participation")
        # Get users who entered trivia on that date
        start_of_day = datetime.combine(draw_date, time(0, 0, 0))
        end_of_day = datetime.combine(draw_date, time(23, 59, 59))
        
        eligible_users_query = (
            db.query(User)
            .join(Entry, User.id == Entry.user_id)
            .filter(
                Entry.created_at.between(start_of_day, end_of_day)
            )
            .distinct()
        )
        
        eligible_users = eligible_users_query.all()
        logging.info(f"Method 2: Found {len(eligible_users)} users who participated in trivia today")
    
    # If still no eligible users, use test users as fallback
    if not eligible_users:
        logging.warning("No eligible users found, using test users as fallback")
        test_users = db.query(User).filter(
            or_(
                User.email.like('%test%'),
                User.email.like('%example%'),
                User.email == 'krishnatrivia@gmail.com'
            )
        ).all()
        
        if test_users:
            logging.info(f"Found {len(test_users)} test users to use as fallback")
            return test_users
        else:
            logging.error("No test users found in the system")
            return []
    
    return eligible_users

def perform_draw(db: Session, draw_date: date = None) -> Dict[str, Any]:
    """
    Perform the daily trivia draw for the specified date (or today if not specified).
    
    This function:
    1. Gets the draw configuration
    2. Determines eligible participants
    3. Randomly selects winners
    4. Distributes prizes
    5. Saves records and returns results
    
    Args:
        db: Database session
        draw_date: Optional date for the draw (defaults to today)
        
    Returns:
        Dictionary with draw results including status, draw_date, total_participants,
        total_winners, prize_pool, and list of winners
    """
    if draw_date is None:
        draw_date = date.today()
    
    logging.info(f"Performing daily draw for date: {draw_date}")
    
    # Get draw configuration
    draw_config = get_draw_config(db)
    
    # Determine winner count from configuration
    if draw_config.is_custom and draw_config.custom_winner_count is not None:
        winner_count = draw_config.custom_winner_count
    else:
        winner_count = draw_config.daily_winners_count
    
    logging.info(f"Draw configuration: winner_count={winner_count}")
    
    # Get eligible participants
    eligible_participants = get_eligible_participants(db, draw_date)
    
    if not eligible_participants:
        logging.warning(f"No eligible participants found for draw date {draw_date}")
        return {
            "status": "no_participants",
            "draw_date": draw_date,
            "total_participants": 0,
            "total_winners": 0,
            "prize_pool": 0,
            "winners": []
        }
    
    total_participants = len(eligible_participants)
    logging.info(f"Found {total_participants} eligible participants")
    
    # Randomly select winners
    actual_winner_count = min(winner_count, total_participants)
    winners = random.sample(eligible_participants, actual_winner_count)
    
    logging.info(f"Selected {len(winners)} winners")
    
    # Always use the calculated pool amount and update the configuration
    total_prize_pool = draw_config.calculated_pool_amount
    draw_config.daily_pool_amount = total_prize_pool  # Update the configuration to match
    db.commit()  # Save the updated configuration
    
    logging.info(f"Using calculated prize pool: ${total_prize_pool}")
    
    individual_prize = round(total_prize_pool / len(winners), 2) if winners and total_prize_pool > 0 else 0
    logging.info(f"Prize distribution: ${individual_prize} per winner")
    
    # Record winners and prepare return data
    winner_records = []
    for position, winner in enumerate(winners, start=1):
        db_winner = TriviaDrawWinner(
            account_id=winner.account_id,
            prize_amount=individual_prize,
            position=position,
            draw_date=draw_date,
            draw_type="daily",
            created_at=datetime.now()
        )
        db.add(db_winner)
        winner_records.append({
            "user_id": winner.account_id,
            "username": winner.username or f"User{winner.account_id}",
            "position": position,
            "prize_amount": individual_prize,
            "draw_date": draw_date.isoformat()
        })
    
    db.commit()
    logging.info(f"Draw completed successfully with {len(winner_records)} winners")
    
    return {
        "status": "success",
        "draw_date": draw_date,
        "total_participants": total_participants,
        "total_winners": len(winner_records),
        "prize_pool": total_prize_pool,
        "winners": winner_records
    }

def get_user_details(user, db: Session) -> Dict[str, Any]:
    """
    Get detailed user information including badges, avatar, and frame.
    """
    # Get badge, avatar, and frame info
    badge_info = db.query(Badge).filter(Badge.id == user.badge_id).first() if user.badge_id else None
    avatar_info = db.query(Avatar).filter(Avatar.id == user.avatar_id).first() if user.avatar_id else None
    frame_info = db.query(Frame).filter(Frame.id == user.frame_id).first() if user.frame_id else None
    
    return {
        "account_id": user.account_id,
        "username": user.username,
        "badge": {
            "id": badge_info.id,
            "name": badge_info.name,
            "image_url": badge_info.image_url
        } if badge_info else None,
        "avatar": {
            "id": avatar_info.id,
            "name": avatar_info.name,
            "image_url": avatar_info.image_url
        } if avatar_info else None,
        "frame": {
            "id": frame_info.id,
            "name": frame_info.name,
            "image_url": frame_info.image_url
        } if frame_info else None
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
        
        # Extract values from nested dictionaries or provide default values
        badge_name = ""
        badge_image_url = ""
        avatar_url = ""
        frame_url = ""
        
        if user_details.get("badge") and isinstance(user_details["badge"], dict):
            badge_name = user_details["badge"].get("name", "") or ""
            badge_image_url = user_details["badge"].get("image_url", "") or ""
        
        if user_details.get("avatar") and isinstance(user_details["avatar"], dict):
            avatar_url = user_details["avatar"].get("image_url", "") or ""
        
        if user_details.get("frame") and isinstance(user_details["frame"], dict):
            frame_url = user_details["frame"].get("image_url", "") or ""
        
        # Create a result with proper string values
        result.append({
            "account_id": user_details.get("account_id", 0),
            "username": user_details.get("username", f"User{user.account_id}"),
            "badge_name": badge_name,
            "badge_image_url": badge_image_url,
            "avatar_url": avatar_url,
            "frame_url": frame_url,
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
    position = 1  # Initialize position counter
    
    for user, weekly_amount in winners_past_week:
        # Get total amount won by this user all-time
        total_amount = db.query(func.sum(TriviaDrawWinner.prize_amount)).filter(
            TriviaDrawWinner.account_id == user.account_id
        ).scalar() or 0.0
        
        # Get user details
        user_details = get_user_details(user, db)
        
        # Extract values from nested dictionaries or provide default values
        badge_name = ""
        badge_image_url = ""
        avatar_url = ""
        frame_url = ""
        
        if user_details.get("badge") and isinstance(user_details["badge"], dict):
            badge_name = user_details["badge"].get("name", "") or ""
            badge_image_url = user_details["badge"].get("image_url", "") or ""
        
        if user_details.get("avatar") and isinstance(user_details["avatar"], dict):
            avatar_url = user_details["avatar"].get("image_url", "") or ""
        
        if user_details.get("frame") and isinstance(user_details["frame"], dict):
            frame_url = user_details["frame"].get("image_url", "") or ""
        
        # Create a result with proper string values
        result.append({
            "account_id": user_details.get("account_id", 0),
            "username": user_details.get("username", f"User{user.account_id}"),
            "badge_name": badge_name,
            "badge_image_url": badge_image_url,
            "avatar_url": avatar_url,
            "frame_url": frame_url,
            "amount_won": weekly_amount,  # For compatibility with WinnerResponse in rewards.py
            "weekly_amount": weekly_amount,  # For compatibility with test_rewards.py
            "total_amount_won": total_amount,
            "position": position  # Added position field
        })
        
        position += 1  # Increment position for next winner
    
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
    position = 1  # Initialize position counter
    
    for user, total_amount in all_time_winners:
        # Get user details
        user_details = get_user_details(user, db)
        
        # Extract values from nested dictionaries or provide default values
        badge_name = ""
        badge_image_url = ""
        avatar_url = ""
        frame_url = ""
        
        if user_details.get("badge") and isinstance(user_details["badge"], dict):
            badge_name = user_details["badge"].get("name", "") or ""
            badge_image_url = user_details["badge"].get("image_url", "") or ""
        
        if user_details.get("avatar") and isinstance(user_details["avatar"], dict):
            avatar_url = user_details["avatar"].get("image_url", "") or ""
        
        if user_details.get("frame") and isinstance(user_details["frame"], dict):
            frame_url = user_details["frame"].get("image_url", "") or ""
        
        # Create a result with proper string values
        result.append({
            "account_id": user_details.get("account_id", 0),
            "username": user_details.get("username", f"User{user.account_id}"),
            "badge_name": badge_name,
            "badge_image_url": badge_image_url,
            "avatar_url": avatar_url,
            "frame_url": frame_url,
            "amount_won": total_amount,  # Same value as total_amount_won for all-time winners
            "total_amount_won": total_amount,
            "position": position  # Added position field
        })
        
        position += 1  # Increment position for next winner
    
    return result 