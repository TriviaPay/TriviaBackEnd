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

from models import User, TriviaQuestionsWinners, TriviaDrawConfig, CompanyRevenue, TriviaQuestionsEntries, Badge, Avatar, Frame, TriviaUserDaily, UserDailyRewards
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
    - User must have answered at least 1 trivia question correctly FOR THE SPECIFIC draw_date
    
    This function checks TriviaUserDaily directly for the draw_date instead of relying on
    the daily_eligibility_flag, which may be stale or from a different day.
    """
    logger.info(f"Getting eligible participants for draw date: {draw_date}")
    
    # Directly query users who:
    # 1. Are subscribed (subscription_flag == True)
    # 2. Have at least 1 correct answer in TriviaUserDaily for the specific draw_date
    
    eligible_users = db.query(User).join(
        TriviaUserDaily,
        and_(
            TriviaUserDaily.account_id == User.account_id,
            TriviaUserDaily.date == draw_date,
            TriviaUserDaily.status == 'answered_correct'
        )
    ).filter(
        User.subscription_flag == True
    ).distinct().all()
    
    # Also validate using TriviaQuestionsEntries as a cross-check
    # This provides additional robustness
    eligible_account_ids = {user.account_id for user in eligible_users}
    
    # Cross-check with TriviaQuestionsEntries
    entries_check = db.query(TriviaQuestionsEntries).filter(
        TriviaQuestionsEntries.date == draw_date,
        TriviaQuestionsEntries.account_id.in_(eligible_account_ids),
        TriviaQuestionsEntries.correct_answers >= 1
    ).all()
    
    entries_account_ids = {entry.account_id for entry in entries_check}
    
    # Use intersection to ensure both sources agree
    # (TriviaUserDaily is source of truth, but entries provides validation)
    if eligible_account_ids and entries_account_ids != eligible_account_ids:
        # Log discrepancy but don't fail - TriviaUserDaily is the authoritative source
        logger.warning(
            f"Eligibility cross-check discrepancy for draw_date {draw_date}: "
            f"TriviaUserDaily found {len(eligible_account_ids)} eligible users, "
            f"TriviaQuestionsEntries found {len(entries_account_ids)} users with correct_answers >= 1"
        )
    
    logger.info(
        f"Found {len(eligible_users)} eligible participants for draw_date {draw_date} "
        f"(subscription_flag=True AND at least 1 correct answer in TriviaUserDaily)"
    )
    
    # Additional diagnostic logging
    if len(eligible_users) == 0:
        # Log why no participants were found
        subscribed_count = db.query(User).filter(User.subscription_flag == True).count()
        users_with_correct_answers = db.query(
            func.count(func.distinct(TriviaUserDaily.account_id))
        ).filter(
            TriviaUserDaily.date == draw_date,
            TriviaUserDaily.status == 'answered_correct'
        ).scalar() or 0
        
        logger.warning(
            f"No eligible participants found for {draw_date}. "
            f"Diagnostics: {subscribed_count} subscribed users, "
            f"{users_with_correct_answers} users with correct answers on {draw_date}"
        )
    
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
    Note: image_url fields have been removed from avatars and frames tables.
    URLs should be generated using presigned URLs from bucket/object_key.
    """
    from utils.storage import presign_get
    
    # Get badge info
    badge_info = db.query(Badge).filter(Badge.id == user.badge_id).first() if user.badge_id else None
    badge_image_url = badge_info.image_url if badge_info else None  # Badge URLs are public, no presigning
    
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

def update_user_eligibility(db: Session, user_account_id: int, draw_date: date) -> None:
    """
    Check if a user has answered 1 question correctly for the given date
    and update their daily_eligibility_flag accordingly.
    Uses trivia_user_daily table.
    
    Note: This flag is informational and may be cleared/reset. The actual eligibility
    check in get_eligible_participants() queries TriviaUserDaily directly to ensure
    accuracy for the specific draw_date.
    """
    # Count correct answers for the user on the given date using TriviaUserDaily
    correct_answers = db.query(TriviaUserDaily).filter(
        TriviaUserDaily.account_id == user_account_id,
        TriviaUserDaily.date == draw_date,
        TriviaUserDaily.status == 'answered_correct'
    ).count()
    
    # Update eligibility flag if user answered 1 question correctly
    # Note: This flag is not date-specific, but helps with quick checks
    # The actual draw eligibility check queries TriviaUserDaily directly
    if correct_answers >= 1:
        db.query(User).filter(User.account_id == user_account_id).update(
            {"daily_eligibility_flag": True}
        )
        db.commit()
        logger.info(
            f"User {user_account_id} eligibility flag updated: "
            f"answered {correct_answers} question(s) correctly on {draw_date}"
        )
    else:
        # Don't set flag to False here - it might be valid for other dates
        # Only set to True when we have confirmed correct answers
        logger.debug(
            f"User {user_account_id} not yet eligible: "
            f"only {correct_answers} correct answer(s) on {draw_date}"
        )