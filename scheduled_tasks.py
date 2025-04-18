"""
Scheduled tasks for the TriviaPayBackend application.
This module contains functions that are run on a schedule.
"""

import logging
import pytz
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from models import User, UserDailyRewards, CompanyRevenue, Transaction
from db import get_db

# Configure logging
logger = logging.getLogger(__name__)

def create_weekly_revenue_snapshot():
    """
    Create a weekly snapshot of company revenue every Monday at 12:00 AM EST.
    This tracks the total revenue and streak rewards paid for the week.
    """
    logger.info("Running scheduled task: create_weekly_revenue_snapshot")
    
    # Get database session
    db = next(get_db())
    
    try:
        # Get EST timezone
        est_tz = pytz.timezone("US/Eastern")
        
        # Get current date in EST
        now_est = datetime.now(est_tz)
        today = now_est.date()
        
        # Calculate the Monday of this week and last week's dates
        current_monday = today - timedelta(days=today.weekday())
        last_monday = current_monday - timedelta(days=7)
        last_sunday = current_monday - timedelta(days=1)
        
        logger.info(f"Creating revenue snapshot for week {last_monday} to {last_sunday}")
        
        # Check if we already have a record for last week
        last_week_record = db.query(CompanyRevenue).filter(
            CompanyRevenue.week_start_date == last_monday
        ).first()
        
        if not last_week_record:
            # Get the total revenue up to the previous week
            previous_total = db.query(func.coalesce(func.max(CompanyRevenue.total_revenue), 0)).scalar()
            previous_streak_rewards = db.query(func.coalesce(func.max(CompanyRevenue.total_streak_rewards_paid), 0)).scalar()
            
            # Calculate the revenue for the last week
            last_week_transactions = db.query(func.sum(Transaction.amount)).filter(
                Transaction.created_at >= last_monday,
                Transaction.created_at < current_monday,
                Transaction.amount > 0  # Only count positive transactions as revenue
            ).scalar() or 0
            
            # Calculate streak rewards paid last week
            last_week_streak_rewards = db.query(func.sum(Transaction.amount)).filter(
                Transaction.created_at >= last_monday,
                Transaction.created_at < current_monday,
                Transaction.transaction_type == "streak_reward"
            ).scalar() or 0
            
            # Create new record for last week
            new_record = CompanyRevenue(
                week_start_date=last_monday,
                week_end_date=last_sunday,
                weekly_revenue=last_week_transactions,
                total_revenue=previous_total + last_week_transactions,
                streak_rewards_paid=last_week_streak_rewards,
                total_streak_rewards_paid=previous_streak_rewards + last_week_streak_rewards,
                notes=f"Auto-generated on {now_est.strftime('%Y-%m-%d %H:%M:%S %Z')}"
            )
            db.add(new_record)
            
            # Create a placeholder record for the current week
            current_week_record = CompanyRevenue(
                week_start_date=current_monday,
                week_end_date=current_monday + timedelta(days=6),
                weekly_revenue=0,
                total_revenue=previous_total + last_week_transactions,
                streak_rewards_paid=0,
                total_streak_rewards_paid=previous_streak_rewards + last_week_streak_rewards,
                notes=f"Initial record for current week"
            )
            db.add(current_week_record)
            
            db.commit()
            logger.info(f"Created revenue snapshot: total={previous_total + last_week_transactions}, weekly={last_week_transactions}")
        else:
            logger.info(f"Revenue snapshot for week {last_monday} already exists")
            
            # Make sure there's a record for the current week
            current_week_record = db.query(CompanyRevenue).filter(
                CompanyRevenue.week_start_date == current_monday
            ).first()
            
            if not current_week_record:
                # Create a placeholder record for the current week
                current_week_record = CompanyRevenue(
                    week_start_date=current_monday,
                    week_end_date=current_monday + timedelta(days=6),
                    weekly_revenue=0,
                    total_revenue=last_week_record.total_revenue,
                    streak_rewards_paid=0,
                    total_streak_rewards_paid=last_week_record.total_streak_rewards_paid,
                    notes=f"Initial record for current week"
                )
                db.add(current_week_record)
                db.commit()
                logger.info(f"Created current week revenue record")
    
    except Exception as e:
        logger.error(f"Error creating weekly revenue snapshot: {str(e)}", exc_info=True)
        db.rollback()
    finally:
        db.close()

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
    },
    {
        "func": create_weekly_revenue_snapshot,
        "id": "create_weekly_revenue_snapshot",
        "day_of_week": "mon",  # Monday
        "hour": 0,  # Midnight
        "minute": 1,  # Run 1 minute after reset_weekly_rewards
        "second": 0,
        "timezone": "US/Eastern"  # Eastern Time
    }
] 