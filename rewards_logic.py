import os
import random
import logging
import json
import calendar
from datetime import datetime, timedelta, date
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_
import pytz

from models import User, TriviaQuestionsWinners, TriviaDrawConfig, TriviaQuestionsAnswers, CompanyRevenue, TriviaQuestionsEntries, Badge, Avatar, Frame
from db import get_db

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_draw_config(db: Session) -> TriviaDrawConfig:
    """
    Get the current draw configuration. Create a default one if it doesn't exist.
    """
    config = db.query(TriviaDrawConfig).first()
    if not config:
        config = TriviaDrawConfig(is_custom=False, custom_winner_count=None)
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

def calculate_winner_count(participant_count: int) -> int:
    """Calculate the number of winners based on participant count."""
    if participant_count <= 0:
        return 0
    elif participant_count < 50:
        return 1
    elif participant_count < 100:
        return 3
    elif participant_count < 200:
        return 5
    elif participant_count < 300:
        return 7
    elif participant_count < 400:
        return 11
    elif participant_count < 500:
        return 13
    elif participant_count < 600:
        return 17
    elif participant_count < 700:
        return 19
    elif participant_count < 800:
        return 23
    elif participant_count < 900:
        return 29
    elif participant_count < 1000:
        return 31
    elif participant_count < 1100:
        return 37
    elif participant_count < 1200:
        return 41
    elif participant_count < 1300:
        return 43
    elif participant_count < 2000:
        return 47
    else:
        return 53  # Cap at 53 winners

def calculate_prize_distribution(total_prize: float, winner_count: int) -> List[float]:
    """Calculate prize distribution using harmonic sum."""
    if winner_count <= 0 or total_prize <= 0:
        return []
    
    # Calculate harmonic sum (1/1 + 1/2 + 1/3 + ... + 1/n)
    harmonic_sum = sum(1/i for i in range(1, winner_count + 1))
    
    # Calculate individual prizes
    prizes = [(1/(i+1))/harmonic_sum * total_prize for i in range(winner_count)]
    
    # Round to 2 decimal places
    return [round(prize, 2) for prize in prizes]

def calculate_prize_pool(db: Session, draw_date: date) -> float:
    """
    Calculate the prize pool for a specific date.
    
    Rules:
    - If subscriber count < 200: monthly_prize_pool = subscriber_count * 4.3
    - If subscriber count >= 200: monthly_prize_pool = subscriber_count * 3.526
      and add (subscriber_count * 0.774) to company revenue
    - daily_prize_pool = monthly_prize_pool / days_in_month
    """
    # Count subscribers
    subscriber_count = db.query(func.count(User.account_id)).filter(
        User.subscription_flag == True
    ).scalar() or 0
    
    logger.info(f"Subscriber count for prize calculation: {subscriber_count}")
    
    # Calculate monthly prize pool based on subscriber count
    if subscriber_count < 200:
        monthly_prize_pool = subscriber_count * 4.3
        company_revenue_amount = 0
    else:
        monthly_prize_pool = subscriber_count * 3.526
        company_revenue_amount = subscriber_count * 0.774
        
        # Add to company revenue table
        month_start = date(draw_date.year, draw_date.month, 1)
        existing_revenue = db.query(CompanyRevenue).filter(
            CompanyRevenue.month_start_date == month_start
        ).first()
        
        if existing_revenue:
            existing_revenue.revenue_amount += company_revenue_amount
            existing_revenue.subscriber_count = subscriber_count
            existing_revenue.updated_at = datetime.utcnow()
        else:
            new_revenue = CompanyRevenue(
                month_start_date=month_start,
                revenue_amount=company_revenue_amount,
                subscriber_count=subscriber_count
            )
            db.add(new_revenue)
        
        db.commit()
        logger.info(f"Added ${company_revenue_amount} to company revenue for {month_start}")
    
    # Get days in current month
    days_in_month = calendar.monthrange(draw_date.year, draw_date.month)[1]
    
    # Calculate daily prize pool
    daily_prize_pool = monthly_prize_pool / days_in_month
    
    logger.info(f"Daily prize pool: ${daily_prize_pool} (monthly: ${monthly_prize_pool}, days: {days_in_month})")
    
    return round(daily_prize_pool, 2)

def get_eligible_participants(db: Session, draw_date: date) -> List[Dict[str, Any]]:
    """
    Get all eligible participants for the daily draw.
    
    Eligibility criteria:
    - User must be subscribed for that month (User.subscription_flag == True)
    - User must have answered 1 trivia question correctly for that day (daily_eligibility_flag == True)
    """
    logger.info(f"Getting eligible participants for draw date: {draw_date}")
    
    # Query users who meet both criteria
    eligible_users = db.query(User).filter(
        User.subscription_flag == True,
        User.daily_eligibility_flag == True
    ).all()
    
    logger.info(f"Found {len(eligible_users)} eligible participants")
    
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
    existing_draw = db.query(TriviaQuestionsWinners).filter(
        TriviaQuestionsWinners.draw_date == draw_date
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
    
    # Get draw configuration
    config = get_draw_config(db)
    
    # Determine number of winners
    if config.is_custom and config.custom_winner_count is not None:
        winner_count = min(config.custom_winner_count, participant_count)
        logger.info(f"Using custom winner count: {winner_count}")
    else:
        winner_count = calculate_winner_count(participant_count)
        logger.info(f"Using calculated winner count: {winner_count}")
    
    logger.info(f"Number of winners: {winner_count}, Eligible participants: {participant_count}")
    
    # Calculate prize pool
    prize_pool = calculate_prize_pool(db, draw_date)
    logger.info(f"Prize pool for draw: ${prize_pool}")
    
    # Calculate prize distribution
    prizes = calculate_prize_distribution(prize_pool, winner_count)
    logger.info(f"Prize distribution: {prizes}")
    
    # Select winners randomly
    if participant_count <= winner_count:
        # If there are fewer participants than winners, everyone wins
        selected_winners = participants
        logger.info("All participants selected as winners (fewer participants than winner slots)")
    else:
        # Otherwise, randomly select winners
        random.shuffle(participants)
        selected_winners = participants[:winner_count]
        logger.info(f"Randomly selected {winner_count} winners from {participant_count} participants")
    
    # Save winners to database
    winners = []
    for i, winner in enumerate(selected_winners):
        position = i + 1
        prize_amount = prizes[i] if i < len(prizes) else 0
        
        winner_record = TriviaQuestionsWinners(
            account_id=winner["account_id"],
            prize_amount=prize_amount,
            position=position,
            draw_date=draw_date
        )
        db.add(winner_record)
        winners.append({
            "account_id": winner["account_id"],
            "username": winner["username"],
            "position": position,
            "prize_amount": prize_amount
        })
    
    db.commit()
    
    # Reset daily eligibility flags after the draw
    reset_daily_eligibility_flags(db)
    
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


def reset_daily_eligibility_flags(db: Session) -> None:
    """
    Reset daily_eligibility_flag for all users after the draw is done.
    This should be called after each daily draw.
    """
    logger.info("Resetting daily eligibility flags for all users")
    
    # Reset all users' daily eligibility flags
    db.query(User).update({"daily_eligibility_flag": False})
    db.commit()
    
    logger.info("Daily eligibility flags reset successfully")

def reset_monthly_subscriptions(db: Session) -> None:
    """
    Reset subscription_flag for all users to False.
    This should be called at 12:01 AM EST on the last day of each month.
    """
    logger.info("Resetting monthly subscription flags for all users")
    
    # Reset all users' subscription flags
    db.query(User).update({"subscription_flag": False})
    db.commit()
    
    logger.info("Monthly subscription flags reset successfully")

def update_user_eligibility(db: Session, user_account_id: int, draw_date: date) -> None:
    """
    Check if a user has answered 1 question correctly for the given date
    and update their daily_eligibility_flag accordingly.
    """
    # Count correct answers for the user on the given date
    correct_answers = db.query(TriviaQuestionsAnswers).filter(
        TriviaQuestionsAnswers.account_id == user_account_id,
        TriviaQuestionsAnswers.date == draw_date,
        TriviaQuestionsAnswers.is_correct == True
    ).count()
    
    # Update eligibility flag if user answered 1 question correctly
    if correct_answers >= 1:
        db.query(User).filter(User.account_id == user_account_id).update(
            {"daily_eligibility_flag": True}
        )
        db.commit()
        logger.info(f"User {user_account_id} is now eligible for draw (answered {correct_answers} questions correctly)")
    else:
        logger.info(f"User {user_account_id} not eligible for draw (only {correct_answers} correct answers)")