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

from models import User, CompanyRevenue, Avatar, Frame, UserDailyRewards
# Legacy tables removed: TriviaQuestionsWinners, TriviaDrawConfig, TriviaQuestionsEntries, TriviaUserDaily, Trivia, TriviaQuestionsDaily
from db import get_db

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Legacy get_draw_config removed - TriviaDrawConfig table deleted
# Use mode-specific draw configuration via TriviaModeConfig instead

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
    import math
    
    if winner_count <= 0 or total_prize <= 0:
        return []
    
    # Calculate harmonic sum (1/1 + 1/2 + 1/3 + ... + 1/n)
    harmonic_sum = sum(1/i for i in range(1, winner_count + 1))
    
    # Calculate individual prizes
    prizes = [(1/(i+1))/harmonic_sum * total_prize for i in range(winner_count)]
    
    # Round down to 2 decimal places (lower limit)
    def round_down(value: float, decimals: int = 2) -> float:
        multiplier = 10 ** decimals
        return math.floor(value * multiplier) / multiplier
    
    return [round_down(prize, 2) for prize in prizes]

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

# Legacy get_eligible_participants removed - TriviaUserDaily and TriviaQuestionsEntries tables deleted
# Use mode-specific eligibility functions instead (e.g., get_eligible_participants_free_mode)

# Legacy perform_draw removed - TriviaQuestionsWinners and TriviaDrawConfig tables deleted
# Use mode-specific draw functions instead (e.g., execute_mode_draw)

def get_user_details(user, db: Session) -> Dict[str, Any]:
    """
    Extract user details for display with cosmetic items (badge, avatar, frame).
    Note: image_url fields have been removed from avatars and frames tables.
    URLs should be generated using presigned URLs from bucket/object_key.
    """
    from utils.storage import presign_get
    
    # Get badge info from TriviaModeConfig (badges merged into trivia_mode_config)
    from models import TriviaModeConfig
    badge_info = db.query(TriviaModeConfig).filter(TriviaModeConfig.mode_id == user.badge_id).first() if user.badge_id else None
    badge_image_url = badge_info.badge_image_url if badge_info and badge_info.badge_image_url else None  # Badge URLs are public, no presigning
    
    # Get avatar URL (presigned)
    avatar_image_url = None
    avatar_id = getattr(user, 'selected_avatar_id', None)
    if avatar_id:
        avatar_info = db.query(Avatar).filter(Avatar.id == avatar_id).first()
        if avatar_info:
            bucket = getattr(avatar_info, "bucket", None)
            object_key = getattr(avatar_info, "object_key", None)
            if bucket and object_key:
                try:
                    avatar_image_url = presign_get(bucket, object_key, expires=900)
                except Exception as e:
                    logging.warning(f"Failed to presign avatar {avatar_info.id}: {e}")
    
    # Get frame URL (presigned)
    frame_image_url = None
    frame_id = getattr(user, 'selected_frame_id', None)
    if frame_id:
        frame_info = db.query(Frame).filter(Frame.id == frame_id).first()
        if frame_info:
            bucket = getattr(frame_info, "bucket", None)
            object_key = getattr(frame_info, "object_key", None)
            if bucket and object_key:
                try:
                    frame_image_url = presign_get(bucket, object_key, expires=900)
                except Exception as e:
                    logging.warning(f"Failed to presign frame {frame_info.id}: {e}")
    
    return {
        "account_id": user.account_id,
        "username": user.username,
        "badge_image_url": badge_image_url,
        "avatar_image_url": avatar_image_url,
        "frame_image_url": frame_image_url
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

def reset_weekly_daily_rewards(db: Session) -> None:
    """
    Reset weekly daily rewards for all users.
    Deletes all UserDailyRewards records from previous weeks.
    This should be called Monday at 00:00 (midnight) in the configured timezone.
    """
    logger.info("Resetting weekly daily rewards for all users")
    
    # Delete all UserDailyRewards records (new week will create fresh records)
    deleted_count = db.query(UserDailyRewards).delete()
    db.commit()
    
    logger.info(f"Weekly daily rewards reset successfully. Deleted {deleted_count} records.")

# Legacy update_user_eligibility removed - TriviaUserDaily table deleted
# Use mode-specific eligibility tracking instead