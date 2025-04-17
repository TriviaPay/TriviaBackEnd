"""
Scheduled tasks for the TriviaPayBackend application.
This module contains functions that are run on a schedule.
"""

import logging
import pytz
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_
from models import User, UserDailyRewards
from db import get_db

# Configure logging
logger = logging.getLogger(__name__)

def reset_weekly_rewards():
    """
    Reset weekly rewards at Monday 12:00 AM EST.
    This marks Day 1 as available and keeps Days 2-7 locked.
    """
    logger.info("Running scheduled task: reset_weekly_rewards")
    
    # Get database session
    db = next(get_db())
    
    try:
        # Get EST timezone
        est_tz = pytz.timezone("US/Eastern")
        
        # Get current date in EST
        now_est = datetime.now(est_tz)
        today = now_est.date()
        
        # Calculate the Monday of this week
        monday_date = today - timedelta(days=today.weekday())
        
        logger.info(f"Resetting weekly rewards for week starting {monday_date}")
        
        # For all users, create or update their weekly rewards record
        users = db.query(User).all()
        logger.info(f"Found {len(users)} users to update")
        
        for user in users:
            # Check if user already has a rewards record for this week
            existing_record = db.query(UserDailyRewards).filter(
                UserDailyRewards.account_id == user.account_id,
                UserDailyRewards.week_start_date == monday_date
            ).first()
            
            if existing_record:
                # Update existing record - mark day 1 as available, all others as locked
                existing_record.day1_status = "available"
                for day in range(2, 8):
                    setattr(existing_record, f"day{day}_status", "locked")
                logger.info(f"Updated existing record for user {user.account_id}")
            else:
                # Create a new record for this week
                new_record = UserDailyRewards(
                    account_id=user.account_id,
                    week_start_date=monday_date,
                    day1_status="available",
                    day2_status="locked",
                    day3_status="locked",
                    day4_status="locked",
                    day5_status="locked",
                    day6_status="locked",
                    day7_status="locked"
                )
                db.add(new_record)
                logger.info(f"Created new weekly record for user {user.account_id}")
        
        # Commit all changes
        db.commit()
        logger.info(f"Weekly rewards reset completed for {len(users)} users")
    
    except Exception as e:
        logger.error(f"Error resetting weekly rewards: {str(e)}", exc_info=True)
        db.rollback()
    finally:
        db.close()

def reset_boost_usage_flags():
    """Reset all users' daily boost usage flags"""
    logger.info("Running scheduled task: reset_boost_usage_flags")
    
    # Get database session
    db = next(get_db())
    
    try:
        # Reset all users' daily boost usage flags
        users = db.query(User).all()
        
        for user in users:
            user.hint_used_today = False
            user.fifty_fifty_used_today = False
            user.auto_answer_used_today = False
        
        db.commit()
        logger.info(f"Successfully reset boost usage flags for {len(users)} users")
    except Exception as e:
        logger.error(f"Error in reset_boost_usage_flags: {str(e)}", exc_info=True)
        db.rollback()
    finally:
        db.close()

# List of scheduled tasks
# Each task should have:
# - func: the function to call
# - id: a unique identifier
# - hour/minute/second/etc: the time to run (CronTrigger parameters)
SCHEDULED_TASKS = [
    {
        "func": reset_boost_usage_flags,
        "id": "reset_boost_usage_flags",
        "hour": 0,  # Midnight
        "minute": 0,
        "second": 0
    },
    {
        "func": reset_weekly_rewards,
        "id": "reset_weekly_rewards",
        "day_of_week": "mon",  # Monday
        "hour": 0,  # Midnight
        "minute": 0,
        "second": 0,
        "timezone": "US/Eastern"  # Eastern Time
    }
] 