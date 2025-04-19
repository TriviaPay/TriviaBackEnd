from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
import logging

from db import get_db
from models import User, UserDailyRewards, Frame, UserFrame, Avatar, UserAvatar, Transaction, Notification, CompanyRevenue
from routers.dependencies import get_current_user, get_admin_user

router = APIRouter(prefix="/daily-rewards", tags=["Daily Rewards"])

# ======== Daily Rewards Endpoints ========

@router.post("/login")
async def process_daily_login(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Process daily login rewards based on the 7-day reward system:
    - Days 1-6: 10 gems
    - Day 7: 30 gems + non-premium frame/avatar
    - Only current day is available to claim
    - Missed days become locked
    - The week resets on Mondays
    - Streaks increment each day, reset if missed (unless streak saver used)
    - $5 reward for every 365-day streak milestone
    """
    logger = logging.getLogger(__name__)

    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get current date and time
    today = datetime.now().date()
    now = datetime.now()
    
    # Find the current weekday (0=Monday, 6=Sunday)
    current_weekday = today.weekday()
    current_day_num = current_weekday + 1  # Convert to 1-7 format
    
    # Log date information for debugging
    logger.info(f"Debug date info - Today: {today}, weekday: {current_weekday}, day_num: {current_day_num}")
    
    # Calculate the Monday of this week
    monday_date = today - timedelta(days=current_weekday)
    
    logger.info(f"Week start date (Monday): {monday_date}")
    
    # Check if user has a daily rewards record for this week
    user_rewards = db.query(UserDailyRewards).filter(
        UserDailyRewards.account_id == user.account_id,
        UserDailyRewards.week_start_date == monday_date
    ).first()
    
    # If no record exists, create one and initialize days
    if not user_rewards:
        logger.info(f"Creating new weekly rewards record for user {user.account_id}")
        user_rewards = UserDailyRewards(
            account_id=user.account_id,
            week_start_date=monday_date,
        )
        
        # Initialize all days with appropriate status
        for day in range(1, 8):
            if day < current_day_num:
                # Past days are missed
                setattr(user_rewards, f"day{day}_status", "missed")
                logger.info(f"Marking previous day{day} as missed")
            elif day == current_day_num:
                # Current day is available
                setattr(user_rewards, f"day{day}_status", "available")
                logger.info(f"Marking current day{day} as available")
            else:
                # Future days are locked
                setattr(user_rewards, f"day{day}_status", "locked")
                logger.info(f"Marking future day{day} as locked")
            
        db.add(user_rewards)
        db.commit()
    else:
        # Reset days to enforce policy - FIXED 
        modified = False
        
        # First ensure all past days that are "available" are set to "missed"
        for day in range(1, current_day_num):
            day_status = getattr(user_rewards, f"day{day}_status")
            if day_status == "available":
                setattr(user_rewards, f"day{day}_status", "missed")
                logger.info(f"Fixed: Changed day{day} from 'available' to 'missed'")
                modified = True
        
        # Then handle the current day
        day_status = getattr(user_rewards, f"day{current_day_num}_status")
        if day_status in ["locked", "missed"]:
            setattr(user_rewards, f"day{current_day_num}_status", "available")
            logger.info(f"Updated day{current_day_num} from {day_status} to available")
            modified = True
        
        # Make sure future days are locked
        for day in range(current_day_num + 1, 8):
            day_status = getattr(user_rewards, f"day{day}_status")
            if day_status == "available":
                setattr(user_rewards, f"day{day}_status", "locked")
                logger.info(f"Fixed: Changed day{day} from 'available' to 'locked'")
                modified = True
                
        if modified:
            db.commit()
    
    # Get today's reward status
    today_status = getattr(user_rewards, f"day{current_day_num}_status")
    logger.info(f"Today (day{current_day_num}) status: {today_status}")
    
    # Check if today's reward already claimed
    if today_status == "claimed" or today_status == "doubled":
        logger.info(f"Daily reward already claimed for user {user.account_id} on day {current_day_num}")
        return {
            "message": "You've already claimed today's reward.",
            "gems_added": 0,
            "current_gems": user.gems,
            "current_streak": user.streaks,
            "daily_reward_status": get_daily_reward_status(user_rewards, current_day_num)
        }
    
    # Check if today's reward is available
    if today_status != "available":
        logger.info(f"Daily reward not available for user {user.account_id} on day {current_day_num}")
        return {
            "message": "No reward available for today.",
            "gems_added": 0,
            "current_gems": user.gems,
            "current_streak": user.streaks,
            "daily_reward_status": get_daily_reward_status(user_rewards, current_day_num)
        }
    
    # Calculate reward amount based on day
    gems_reward = 30 if current_day_num == 7 else 10
    
    # Award the gems
    user.gems += gems_reward
    
    # Mark today's reward as claimed
    setattr(user_rewards, f"day{current_day_num}_status", "claimed")
    
    # Special rewards for day 7 (Sunday)
    special_reward_message = ""
    if current_day_num == 7:
        # Award a non-premium frame or avatar
        frame_awarded = award_nonpremium_cosmetic(user, db, logger)
        if frame_awarded:
            special_reward_message = f" and a new {frame_awarded['type']} ({frame_awarded['name']})"
    
    # Reset daily boost usage flags
    user.hint_used_today = False
    user.fifty_fifty_used_today = False
    user.auto_answer_used_today = False
    logger.info(f"Reset daily boost flags for user {user.account_id}")

    # Commit all changes
    try:
        db.commit()
        logger.info(f"Committed daily login updates for user {user.account_id}")
        
        # Build response message
        reward_message = f"You received {gems_reward} gems{special_reward_message}!"
        
        return {
            "message": reward_message,
            "gems_added": gems_reward,
            "current_gems": user.gems,
            "current_streak": user.streaks,
            "daily_reward_status": get_daily_reward_status(user_rewards, current_day_num)
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to commit daily login for user {user.account_id}: {e}", exc_info=True)
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Failed to process daily login: {str(e)}"
        )

@router.post("/double-up")
async def process_double_up_reward(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Process the "Double Up" functionality after watching an ad.
    - Only works if the day's reward has already been claimed
    - Adds the same amount of gems again (10 or 30)
    """
    logger = logging.getLogger(__name__)

    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get current date
    today = datetime.now().date()
    
    # Find the current weekday (0=Monday, 6=Sunday)
    current_weekday = today.weekday()
    current_day_num = current_weekday + 1  # Convert to 1-7 format
    
    # Log date information for debugging
    logger.info(f"Debug date info - Today: {today}, weekday: {current_weekday}, day_num: {current_day_num}")
    
    # Calculate the Monday of this week
    monday_date = today - timedelta(days=current_weekday)
    
    logger.info(f"Week start date (Monday): {monday_date}")
    
    # Get user's rewards for this week
    user_rewards = db.query(UserDailyRewards).filter(
        UserDailyRewards.account_id == user.account_id,
        UserDailyRewards.week_start_date == monday_date
    ).first()
    
    if not user_rewards:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="No rewards claimed this week"
        )
    
    # Get today's reward status
    today_status = getattr(user_rewards, f"day{current_day_num}_status")
    logger.info(f"Today (day{current_day_num}) status for double-up: {today_status}")
    
    # Verify the reward was claimed but not yet doubled
    if today_status != "claimed":
        if today_status == "doubled":
            return {
                "message": "You've already doubled today's reward!",
                "gems_added": 0,
                "current_gems": user.gems,
                "daily_reward_status": get_daily_reward_status(user_rewards, current_day_num)
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="Today's reward hasn't been claimed yet"
            )
    
    # Calculate the reward to double (same as original)
    gems_reward = 30 if current_day_num == 7 else 10
    
    # Add the gems
    user.gems += gems_reward
    
    # Mark as doubled
    setattr(user_rewards, f"day{current_day_num}_status", "doubled")
    
    # Commit changes
    try:
        db.commit()
        logger.info(f"Doubled daily reward for user {user.account_id} on day {current_day_num}")
        
        return {
            "message": f"Doubled today's reward! You received {gems_reward} additional gems!",
            "gems_added": gems_reward,
            "current_gems": user.gems,
            "daily_reward_status": get_daily_reward_status(user_rewards, current_day_num)
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to double reward for user {user.account_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Failed to process double-up reward: {str(e)}"
        )

@router.get("/weekly-status")
async def get_weekly_rewards_status(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get the current status of the weekly rewards.
    Returns the status of all 7 days in the current week.
    Only the current day is available to claim if not already claimed.
    Past days are marked as missed and future days are locked.
    """
    logger = logging.getLogger(__name__)

    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get current date
    today = datetime.now().date()
    
    # Find the current weekday (0=Monday, 6=Sunday)
    current_weekday = today.weekday()
    current_day_num = current_weekday + 1  # Convert to 1-7 format
    
    # Log date information for debugging
    logger.info(f"Debug date info - Today: {today}, weekday: {current_weekday}, day_num: {current_day_num}")
    
    # Calculate the Monday of this week
    monday_date = today - timedelta(days=current_weekday)
    
    logger.info(f"Week start date (Monday): {monday_date}")
    
    # Check if user has a daily rewards record for this week
    user_rewards = db.query(UserDailyRewards).filter(
        UserDailyRewards.account_id == user.account_id,
        UserDailyRewards.week_start_date == monday_date
    ).first()
    
    # If no record exists, create one and initialize days
    if not user_rewards:
        logger.info(f"Creating new weekly rewards record for user {user.account_id}")
        user_rewards = UserDailyRewards(
            account_id=user.account_id,
            week_start_date=monday_date,
        )
        
        # Initialize all days with appropriate status
        for day in range(1, 8):
            if day < current_day_num:
                # Past days are missed
                setattr(user_rewards, f"day{day}_status", "missed")
                logger.info(f"Marking previous day{day} as missed")
            elif day == current_day_num:
                # Current day is available
                setattr(user_rewards, f"day{day}_status", "available")
                logger.info(f"Marking current day{day} as available")
            else:
                # Future days are locked
                setattr(user_rewards, f"day{day}_status", "locked")
                logger.info(f"Marking future day{day} as locked")
            
        db.add(user_rewards)
        db.commit()
    else:
        # Reset days to enforce policy - FIXED
        modified = False
        
        # First ensure all past days that are "available" are set to "missed"
        for day in range(1, current_day_num):
            day_status = getattr(user_rewards, f"day{day}_status")
            if day_status == "available":
                setattr(user_rewards, f"day{day}_status", "missed")
                logger.info(f"Fixed: Changed day{day} from 'available' to 'missed'")
                modified = True
        
        # Then handle the current day
        day_status = getattr(user_rewards, f"day{current_day_num}_status")
        if day_status in ["locked", "missed"]:
            setattr(user_rewards, f"day{current_day_num}_status", "available")
            logger.info(f"Updated day{current_day_num} from {day_status} to available")
            modified = True
        
        # Make sure future days are locked
        for day in range(current_day_num + 1, 8):
            day_status = getattr(user_rewards, f"day{day}_status")
            if day_status == "available":
                setattr(user_rewards, f"day{day}_status", "locked")
                logger.info(f"Fixed: Changed day{day} from 'available' to 'locked'")
                modified = True
                
        if modified:
            db.commit()
    
    # Create response with daily reward statuses
    return get_daily_reward_status(user_rewards, current_day_num)

@router.post("/update-streak")
async def update_streak(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update the user's login streak.
    - Streaks increment for consecutive day logins
    - Streak resets if more than one day is missed
    - Streak saver can be used to maintain streak after missing one day
    - $5 reward for every 365-day streak milestone
    - This endpoint can only update the streak once per day
    """
    logger = logging.getLogger(__name__)

    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get current date and time
    today = datetime.now().date()
    now = datetime.now()
    
    # Calculate the Monday of this week
    current_weekday = today.weekday()
    monday_date = today - timedelta(days=current_weekday)
    
    # Check if streak was already updated today
    if user.last_streak_date and user.last_streak_date.date() == today:
        return {
            "message": "Streak already updated today.",
            "current_streak": user.streaks,
            "streak_saver_count": user.streak_saver_count,
            "streak_updated": False
        }
    
    streak_message = ""
    streak_reward_message = ""
    streak_saver_used = False
    streak_updated = False
    
    # Check if this is a consecutive day login 
    if user.last_streak_date:
        yesterday = today - timedelta(days=1)
        two_days_ago = today - timedelta(days=2)
        
        if user.last_streak_date.date() == yesterday:
            # Consecutive day, increment streak
            user.streaks += 1
            streak_updated = True
            logger.info(f"User {user.account_id} continued streak to {user.streaks}")
            
            # Check for 365-day milestone
            if user.streaks > 0 and user.streaks % 365 == 0:
                # Award $5 bonus for yearly streak
                reward_amount = 5.0
                user.wallet_balance += reward_amount
                
                # Create transaction record
                streak_transaction = Transaction(
                    account_id=user.account_id,
                    transaction_type="streak_reward",
                    amount=reward_amount,
                    description=f"Reward for {user.streaks}-day streak"
                )
                db.add(streak_transaction)
                
                # Create notification
                milestone_notification = Notification(
                    account_id=user.account_id,
                    notification_type="streak_milestone",
                    message=f"Congratulations on your {user.streaks}-day streak! You've earned a $5.00 bonus."
                )
                db.add(milestone_notification)
                
                # Update company revenue
                current_week_revenue = db.query(CompanyRevenue).filter(
                    CompanyRevenue.week_start_date == monday_date
                ).first()
                
                if current_week_revenue:
                    current_week_revenue.streak_rewards_paid += reward_amount
                    current_week_revenue.total_streak_rewards_paid += reward_amount
                
                streak_reward_message = f"You've earned a $5.00 bonus for your {user.streaks}-day streak!"
                logger.info(f"User {user.account_id} awarded ${reward_amount} for {user.streaks}-day streak")
            
            # Check for monthly milestone (30 days) for notification
            if user.streaks > 0 and user.streaks % 30 == 0:
                month_milestone = user.streaks // 30
                monthly_notification = Notification(
                    account_id=user.account_id,
                    notification_type="streak_milestone",
                    message=f"Congratulations on your {month_milestone}-month streak! Keep it up!"
                )
                db.add(monthly_notification)
        
        elif user.last_streak_date.date() == two_days_ago and user.streak_saver_count > 0:
            # Missed one day but has streak saver
            user.streak_saver_count -= 1
            streak_saver_used = True
            user.streaks += 1
            streak_updated = True
            streak_message = f"Streak saver used. You have {user.streak_saver_count} streak savers remaining."
            logger.info(f"Used streak saver for user {user.account_id}. Remaining savers: {user.streak_saver_count}")
        
        else:
            # Missed more than one day or no streak saver available
            user.streaks = 1  # Reset streak and start fresh
            streak_updated = True
            logger.info(f"Streak reset for user {user.account_id}. More than one day missed.")
    else:
        # First login ever
        user.streaks = 1
        streak_updated = True
        logger.info(f"User {user.account_id} started first streak.")
    
    # Update last streak date
    user.last_streak_date = now

    # Commit all changes
    try:
        db.commit()
        logger.info(f"Committed streak update for user {user.account_id}")
        
        # Build response message
        response_message = "Streak updated successfully."
        if streak_message:
            response_message = streak_message
        if streak_reward_message:
            response_message = streak_reward_message
        
        return {
            "message": response_message,
            "current_streak": user.streaks,
            "streak_saver_count": user.streak_saver_count,
            "streak_saver_used": streak_saver_used,
            "streak_updated": streak_updated
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to update streak for user {user.account_id}: {e}", exc_info=True)
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Failed to update streak: {str(e)}"
        )

@router.get("/streak-info")
async def get_streak_info(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get the user's current streak information, streak savers, and streak-related notifications.
    """
    logger = logging.getLogger(__name__)

    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get streak-related notifications
    notifications = db.query(Notification).filter(
        Notification.account_id == user.account_id,
        Notification.notification_type == "streak_milestone",
        Notification.is_read == False
    ).order_by(Notification.created_at.desc()).limit(5).all()
    
    # Calculate days until next milestone
    next_monthly_milestone = 30 * (user.streaks // 30 + 1)
    days_until_monthly = next_monthly_milestone - user.streaks
    
    next_yearly_milestone = 365 * (user.streaks // 365 + 1)
    days_until_yearly = next_yearly_milestone - user.streaks
    
    # Calculate next reward date
    next_reward_date = None
    if user.last_streak_date:
        # Instead of adding days_until_yearly to last_streak_date (which is when they last 
        # logged in), we need to calculate when they'll reach the next yearly milestone
        # This is today + the remaining days until yearly milestone
        today = datetime.now().date()
        next_reward_date = (today + timedelta(days=days_until_yearly)).isoformat()
    
    notification_list = []
    for notification in notifications:
        notification_list.append({
            "id": notification.id,
            "message": notification.message,
            "created_at": notification.created_at.isoformat()
        })
    
    return {
        "current_streak": user.streaks,
        "streak_saver_count": user.streak_saver_count,
        "days_until_monthly_milestone": days_until_monthly,
        "days_until_yearly_milestone": days_until_yearly,
        "next_yearly_milestone": next_yearly_milestone,
        "next_reward_date": next_reward_date,
        "notifications": notification_list
    }

@router.post("/notifications/{notification_id}/mark-read")
async def mark_notification_read(
    notification_id: int,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Mark a notification as read.
    """
    logger = logging.getLogger(__name__)

    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.account_id == user.account_id
    ).first()
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    notification.is_read = True
    db.commit()
    
    return {"message": "Notification marked as read"}

@router.post("/use-streak-saver", 
    summary="Manually use a streak saver",
    description="""
    Manually use a streak saver from your inventory to maintain your streak after missing a day.
    
    **Requirements:**
    - User must have at least one streak saver in inventory
    - User must have missed 1-2 days since last login (not more)
    - User must have a previous streak to save
    - User must not have already logged in today
    
    When successful, this endpoint:
    1. Decrements the streak saver count by 1
    2. Increments the user's streak by 1
    3. Updates the last streak date to today
    
    **Note:** This endpoint is different from purchasing and automatically using a streak saver in the store.
    It allows you to use a streak saver you've already purchased.
    """,
    responses={
        200: {
            "description": "Streak saver used successfully",
            "content": {
                "application/json": {
                    "example": {
                        "message": "Streak saver used successfully. Your streak is now 45 days.",
                        "current_streak": 45,
                        "streak_saver_count": 2,
                        "last_streak_date": "2023-06-15T10:30:00"
                    }
                }
            }
        },
        400: {
            "description": "Cannot use streak saver",
            "content": {
                "application/json": {
                    "examples": {
                        "no_savers": {
                            "summary": "No streak savers available",
                            "value": {"detail": "No streak savers available. Please purchase one from the store."}
                        },
                        "already_updated": {
                            "summary": "Already logged in today",
                            "value": {"detail": "You've already logged in today. Your streak is already active."}
                        },
                        "too_late": {
                            "summary": "Too many days missed",
                            "value": {"detail": "It's been 5 days since your last login. Streak saver can only help if you missed 1 or 2 days."}
                        }
                    }
                }
            }
        }
    }
)
async def use_streak_saver(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Manually use a streak saver to prevent losing the current streak.
    This will:
    1. Check if the user has streak savers available
    2. Verify that the user's streak needs saving (last login wasn't today)
    3. Apply the streak saver and keep the streak active
    
    Returns the updated streak saver count and streak info.
    """
    logger = logging.getLogger(__name__)

    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get current date and time
    today = datetime.now().date()
    now = datetime.now()
    
    # Check if streak saver is available
    if user.streak_saver_count <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No streak savers available. Please purchase one from the store."
        )
    
    # Check if streak was already updated today
    if user.last_streak_date and user.last_streak_date.date() == today:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You've already logged in today. Your streak is already active."
        )
    
    # Check if user has a streak to save
    if not user.last_streak_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active streak to save."
        )
    
    # Calculate days since last streak update
    days_since_last_login = (today - user.last_streak_date.date()).days
    
    # If more than 2 days have passed, streak saver can't help
    if days_since_last_login > 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"It's been {days_since_last_login} days since your last login. Streak saver can only help if you missed 1 or 2 days."
        )
    
    # Use streak saver - increment streak and set last_streak_date to yesterday
    user.streak_saver_count -= 1
    user.streaks += 1
    user.last_streak_date = datetime.now()  # Update to today to maintain streak
    
    try:
        db.commit()
        logger.info(f"Streak saver used for user {user.account_id}. Streak extended to {user.streaks}.")
        
        return {
            "message": f"Streak saver used successfully. Your streak is now {user.streaks} days.",
            "current_streak": user.streaks,
            "streak_saver_count": user.streak_saver_count,
            "last_streak_date": user.last_streak_date.isoformat()
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to use streak saver for user {user.account_id}: {e}", exc_info=True)
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to use streak saver: {str(e)}"
        )

# Admin endpoint to reset daily rewards records
@router.post("/admin/reset-rewards")
async def reset_daily_rewards(
    claims: dict = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    Admin endpoint to reset and fix daily rewards records.
    This will ensure that for each user's current week record:
    - Only the current day is available
    - Past days are marked as missed if not claimed
    - Future days are locked
    """
    logger = logging.getLogger(__name__)
    
    try:
        # Get current date
        today = datetime.now().date()
        
        # Find the current weekday (0=Monday, 6=Sunday)
        current_weekday = today.weekday()
        current_day_num = current_weekday + 1  # Convert to 1-7 format
        
        # Calculate the Monday of this week
        monday_date = today - timedelta(days=current_weekday)
        
        # Get all current week rewards records
        records = db.query(UserDailyRewards).filter(
            UserDailyRewards.week_start_date == monday_date
        ).all()
        
        logger.info(f"Found {len(records)} records for current week")
        
        fixed_count = 0
        
        for record in records:
            modified = False
            
            # Fix past days (should be missed or claimed/doubled)
            for day in range(1, current_day_num):
                day_status = getattr(record, f"day{day}_status")
                if day_status == "available" or day_status == "locked":
                    setattr(record, f"day{day}_status", "missed")
                    modified = True
            
            # Fix current day (should be available if not claimed/doubled)
            day_status = getattr(record, f"day{current_day_num}_status")
            if day_status == "locked" or day_status == "missed":
                setattr(record, f"day{current_day_num}_status", "available")
                modified = True
            
            # Fix future days (should all be locked)
            for day in range(current_day_num + 1, 8):
                day_status = getattr(record, f"day{day}_status")
                if day_status != "locked":
                    setattr(record, f"day{day}_status", "locked")
                    modified = True
            
            if modified:
                fixed_count += 1
        
        db.commit()
        
        return {
            "message": f"Daily rewards reset complete. Fixed {fixed_count} out of {len(records)} records.",
            "current_day": current_day_num,
            "week_start_date": monday_date.isoformat()
        }
    
    except Exception as e:
        logger.error(f"Error resetting daily rewards: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset daily rewards: {str(e)}"
        )

# Helper function to award a non-premium cosmetic item
def award_nonpremium_cosmetic(user, db, logger):
    """Award a non-premium frame or avatar based on sequential ID."""
    try:
        # Try to award a frame first
        frames = db.query(Frame).filter(
            Frame.is_premium == False,
            ~Frame.id.in_(db.query(UserFrame.frame_id).filter(UserFrame.account_id == user.account_id))
        ).order_by(Frame.id).limit(1).all()
        
        if frames:
            # Award the frame
            frame = frames[0]
            user_frame = UserFrame(
                account_id=user.account_id,
                frame_id=frame.id,
                unlock_date=datetime.now()
            )
            db.add(user_frame)
            logger.info(f"Awarded frame {frame.id} to user {user.account_id}")
            return {"type": "frame", "name": frame.name, "id": frame.id}
        
        # If no frames, try avatars
        avatars = db.query(Avatar).filter(
            Avatar.is_premium == False,
            ~Avatar.id.in_(db.query(UserAvatar.avatar_id).filter(UserAvatar.account_id == user.account_id))
        ).order_by(Avatar.id).limit(1).all()
        
        if avatars:
            # Award the avatar
            avatar = avatars[0]
            user_avatar = UserAvatar(
                account_id=user.account_id,
                avatar_id=avatar.id,
                unlock_date=datetime.now()
            )
            db.add(user_avatar)
            logger.info(f"Awarded avatar {avatar.id} to user {user.account_id}")
            return {"type": "avatar", "name": avatar.name, "id": avatar.id}
        
        # User already has all non-premium items
        logger.info(f"User {user.account_id} already has all non-premium cosmetics")
        return None
    except Exception as e:
        logger.error(f"Error awarding cosmetic to user {user.account_id}: {e}")
        return None

# Helper function to format daily reward status response
def get_daily_reward_status(user_rewards, current_day_num):
    """Format the daily rewards status for the response."""
    rewards_status = {}
    
    for day in range(1, 8):
        day_status = getattr(user_rewards, f"day{day}_status")
        reward_amount = 30 if day == 7 else 10
        doubled = day_status == "doubled"
        
        rewards_status[f"day{day}"] = {
            "status": day_status,
            "reward_amount": reward_amount,
            "doubled": doubled,
            "is_today": day == current_day_num,
            "special_reward": day == 7  # Day 7 has higher gem reward and cosmetic
        }
    
    return {
        "current_day": current_day_num,
        "week_start_date": user_rewards.week_start_date.isoformat(),
        "days": rewards_status
    } 