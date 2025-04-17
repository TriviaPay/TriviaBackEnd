from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
import logging

from db import get_db
from models import User, UserDailyRewards, Frame, UserFrame, Avatar, UserAvatar
from routers.dependencies import get_current_user

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
    """
    logger = logging.getLogger(__name__)

    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get current date and time
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
            "daily_reward_status": get_daily_reward_status(user_rewards, current_day_num)
        }
    
    # Check if today's reward is available
    if today_status != "available":
        logger.info(f"Daily reward not available for user {user.account_id} on day {current_day_num}")
        return {
            "message": "No reward available for today.",
            "gems_added": 0,
            "current_gems": user.gems,
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
    
    # Create response with daily reward statuses
    return get_daily_reward_status(user_rewards, current_day_num)

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