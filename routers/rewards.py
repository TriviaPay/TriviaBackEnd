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
from models import User, TriviaDrawWinner, TriviaDrawConfig, DailyQuestion, Trivia, Badge, Avatar, Frame, Entry, UserFrame, UserAvatar
from routers.dependencies import get_current_user, get_admin_user
from routers.winners import WinnerResponse
from sqlalchemy.sql import extract
import os
import json
import logging

router = APIRouter(tags=["Rewards"])

# ======== Models ========

class DrawConfigResponse(BaseModel):
    is_custom: bool
    custom_winner_count: Optional[int] = None
    draw_time_hour: int = 20
    draw_time_minute: int = 0
    draw_timezone: str = "US/Eastern"
    custom_data: Optional[Dict[str, Any]] = None

class DrawResponse(BaseModel):
    total_participants: int
    total_winners: int
    winners: List[WinnerResponse]
    prize_pool: float

class DrawConfigUpdateRequest(BaseModel):
    draw_time_hour: Optional[int] = Field(None, ge=0, le=23, description="Hour for daily draw (0-23)")
    draw_time_minute: Optional[int] = Field(None, ge=0, le=59, description="Minute for daily draw (0-59)")
    draw_timezone: Optional[str] = Field(None, description="Timezone for daily draw (e.g., 'US/Eastern')")

# ======== Helper Functions ========

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
    """Calculate the prize pool for a specific date."""
    # Count subscribers
    subscriber_count = db.query(func.count(User.account_id)).filter(
        User.subscription_flag == True
    ).scalar() or 0
    
    # Calculate total monthly prize pool
    monthly_prize_pool = subscriber_count * 3.526
    
    # Get days in current month
    days_in_month = calendar.monthrange(draw_date.year, draw_date.month)[1]
    
    # Calculate daily prize pool
    daily_prize_pool = monthly_prize_pool / days_in_month
    
    return round(daily_prize_pool, 2)

def get_eligible_users(db, date_for_entries):
    """
    Get users who completed at least one question for the given date.
    """
    logging.info(f"Finding eligible users for draw on {date_for_entries}")
    
    # Get all users with entries for the given date
    users_with_entries = (
        db.query(User)
        .join(Entry, User.account_id == Entry.account_id)
        .filter(Entry.date == date_for_entries)
        .filter(Entry.correct_answers > 0)  # Must have at least 1 correct answer
        .all()
    )
    
    logging.info(f"Found {len(users_with_entries)} users with at least 1 correct answer")
    
    # Filter for active users (exclude banned, etc.)
    active_users = [user for user in users_with_entries if is_user_active(user)]
    
    logging.info(f"Found {len(active_users)} active eligible users")
    
    return active_users

def is_user_active(user):
    """
    Check if a user is active (not banned, etc.)
    """
    # Add any additional checks here as needed
    # For now, all users are considered active
    return True

# ======== API Endpoints ========

@router.post("/admin/trigger-draw", response_model=DrawResponse)
async def trigger_draw(
    draw_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to trigger a draw for a specific date.
    If no date is provided, triggers a draw for the current date.
    """
    try:
        # Determine the draw date
        if draw_date:
            target_date = datetime.strptime(draw_date, "%Y-%m-%d").date()
        else:
            est = pytz.timezone('US/Eastern')
            target_date = datetime.now(est).date()
        
        logging.info(f"Triggering draw for date: {target_date}")
        
        # Check if a draw has already been performed for this date
        existing_draw = db.query(TriviaDrawWinner).filter(
            TriviaDrawWinner.draw_date == target_date
        ).first()
        
        if existing_draw:
            logging.warning(f"Draw already performed for {target_date}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A draw has already been performed for {target_date}"
            )
        
        # Get eligible users
        eligible_users = get_eligible_users(db, target_date)
        participant_count = len(eligible_users)
        
        logging.info(f"Found {participant_count} eligible participants for the draw")
        
        if participant_count == 0:
            logging.warning(f"No eligible participants found for draw on {target_date}")
            return DrawResponse(
                total_participants=0,
                total_winners=0,
                winners=[],
                prize_pool=0
            )
        
        # Get draw configuration
        config = db.query(TriviaDrawConfig).first()
        logging.info(f"Using draw config: is_custom={config.is_custom if config else False}, custom_winner_count={config.custom_winner_count if config else None}")
    
        # Determine number of winners
        if config and config.is_custom and config.custom_winner_count is not None:
            winner_count = min(config.custom_winner_count, participant_count)
            logging.info(f"Using custom winner count: {winner_count}")
        else:
            winner_count = calculate_winner_count(participant_count)
            logging.info(f"Using calculated winner count: {winner_count}")
        
        # Calculate prize pool
        prize_pool = calculate_prize_pool(db, target_date)
        logging.info(f"Prize pool for draw: ${prize_pool}")
        
        # Calculate prize distribution
        prizes = calculate_prize_distribution(prize_pool, winner_count)
        logging.info(f"Prize distribution: {prizes}")
        
        # Select winners randomly
        if participant_count <= winner_count:
            # If there are fewer participants than winners, everyone wins
            winners = eligible_users
            logging.info("All participants selected as winners (fewer participants than winner slots)")
        else:
            # Otherwise, randomly select winners
            winners = random.sample(eligible_users, winner_count)
            logging.info(f"Randomly selected {winner_count} winners from {participant_count} participants")
        
        # Save winners to database
        winner_responses = []
        
        for i, winner in enumerate(winners):
            if i < len(prizes):
                prize_amount = prizes[i]
                logging.info(f"Processing winner {i+1}: User {winner.account_id} ({winner.username}), prize: ${prize_amount}")
                
                # Save to database
                draw_winner = TriviaDrawWinner(
                    account_id=winner.account_id,
                    prize_amount=prize_amount,
                    position=i+1,
                    draw_date=target_date
                )
                db.add(draw_winner)
                
                # Update user's wallet balance
                winner.wallet_balance = (winner.wallet_balance or 0) + prize_amount
                winner.last_wallet_update = datetime.utcnow()
                
                # Calculate total amount won by user all-time (including this win)
                total_won = db.query(func.sum(TriviaDrawWinner.prize_amount)).filter(
                    TriviaDrawWinner.account_id == winner.account_id
                ).scalar() or 0
                total_won += prize_amount
                
                # Get badge information
                badge_name = None
                badge_image_url = None
                if hasattr(winner, 'badge_info') and winner.badge_info:
                    badge_name = winner.badge_info.name
                    badge_image_url = winner.badge_image_url
                
                # Get avatar URL
                avatar_url = None
                if hasattr(winner, 'selected_avatar_id') and winner.selected_avatar_id:
                    avatar_query = text("""
                        SELECT image_url FROM avatars 
                        WHERE id = :avatar_id
                    """)
                    avatar_result = db.execute(avatar_query, {"avatar_id": winner.selected_avatar_id}).first()
                    if avatar_result:
                        avatar_url = avatar_result[0]
                
                # Get frame URL
                frame_url = None
                if hasattr(winner, 'selected_frame_id') and winner.selected_frame_id:
                    frame_query = text("""
                        SELECT image_url FROM frames 
                        WHERE id = :frame_id
                    """)
                    frame_result = db.execute(frame_query, {"frame_id": winner.selected_frame_id}).first()
                    if frame_result:
                        frame_url = frame_result[0]
                
                winner_responses.append(WinnerResponse(
                    username=winner.username or f"User{winner.account_id}",
                    amount_won=prize_amount,
                    total_amount_won=total_won,
                    badge_name=badge_name,
                    badge_image_url=badge_image_url,
                    avatar_url=avatar_url,
                    frame_url=frame_url,
                    position=i+1
                ))
        
        db.commit()
        logging.info(f"Draw completed successfully with {len(winner_responses)} winners")
        
        return DrawResponse(
            total_participants=participant_count,
            total_winners=len(winner_responses),
            winners=winner_responses,
            prize_pool=prize_pool
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Error in trigger_draw: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error triggering draw: {str(e)}"
        )

@router.put("/admin/reset-winner-logic", response_model=DrawConfigResponse)
async def reset_winner_logic(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to reset to the default winner logic.
    Updates the latest config record ensuring consistency with GET /admin/draw-config.
    """
    # Ensure logger is accessible within the function scope
    logger = logging.getLogger(__name__)
    try:
        logger.info("--- Entering reset_winner_logic ---")
        # Fetch the LATEST config record
        config = db.query(TriviaDrawConfig).order_by(TriviaDrawConfig.id.desc()).first()

        # Define default values within the function scope to ensure availability
        DEFAULT_DRAW_HOUR = int(os.environ.get("DRAW_TIME_HOUR", "20"))
        DEFAULT_DRAW_MINUTE = int(os.environ.get("DRAW_TIME_MINUTE", "0"))
        DEFAULT_TIMEZONE = os.environ.get("DRAW_TIMEZONE", "US/Eastern")

        if not config:
            # If no config exists, create one with defaults
            logger.info("No config found, creating new default config.")
            config = TriviaDrawConfig(
                is_custom=False,
                custom_winner_count=None,
                custom_data=json.dumps({ # Initialize custom_data with defaults
                    "draw_time_hour": DEFAULT_DRAW_HOUR,
                    "draw_time_minute": DEFAULT_DRAW_MINUTE,
                    "draw_timezone": DEFAULT_TIMEZONE
                })
            )
            db.add(config)
        else:
            # If config exists, update it
            logger.info(f"Found config ID={config.id}. Resetting is_custom and custom_winner_count.")
            config.is_custom = False
            config.custom_winner_count = None
            # Note: We don't reset the draw time/timezone in custom_data here,
            # as resetting only affects the winner count logic.
        
        db.commit()
        db.refresh(config) # Refresh to get the committed state
        logger.info(f"Committed reset. DB state: ID={config.id}, is_custom={config.is_custom}, count={config.custom_winner_count}, custom_data='{config.custom_data}'")

        # --- Read response values from the refreshed DB state --- 
        final_custom_data = {}
        if config.custom_data:
            try:
                final_custom_data = json.loads(config.custom_data)
            except json.JSONDecodeError:
                 logger.error(f"Failed to parse custom_data after reset ID={config.id}: {config.custom_data}. Using defaults for response.", exc_info=True)
                 final_custom_data = {
                     "draw_time_hour": DEFAULT_DRAW_HOUR,
                     "draw_time_minute": DEFAULT_DRAW_MINUTE,
                     "draw_timezone": DEFAULT_TIMEZONE
                 }
        else: # Handle case where custom_data might be None
             logger.warning(f"custom_data is None after reset for ID={config.id}. Using defaults for response time/tz.")
             final_custom_data = {
                 "draw_time_hour": DEFAULT_DRAW_HOUR,
                 "draw_time_minute": DEFAULT_DRAW_MINUTE,
                 "draw_timezone": DEFAULT_TIMEZONE
             }

        # Get time/timezone from the parsed custom_data, falling back to defaults
        resp_hour = final_custom_data.get("draw_time_hour", DEFAULT_DRAW_HOUR)
        resp_minute = final_custom_data.get("draw_time_minute", DEFAULT_DRAW_MINUTE)
        resp_timezone = final_custom_data.get("draw_timezone", DEFAULT_TIMEZONE)

        logger.info(f"Final reset_winner_logic response values: is_custom={config.is_custom}, count={config.custom_winner_count}, hour={resp_hour}, min={resp_minute}, tz={resp_timezone}")

        # Construct the response using DB values and include custom_data
        return DrawConfigResponse(
            is_custom=config.is_custom,
            custom_winner_count=config.custom_winner_count,
            draw_time_hour=resp_hour,
            draw_time_minute=resp_minute,
            draw_timezone=resp_timezone,
            custom_data=final_custom_data # Include the parsed custom_data
        )
        
    except Exception as e:
        # Ensure logger is accessible in except block too
        logger = logging.getLogger(__name__)
        db.rollback()
        logger.error(f"Error resetting winner logic: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error resetting winner logic: {str(e)}"
        )

# ======== Daily Rewards Endpoints ========

# These endpoints have been moved to routers/daily_rewards.py
# - /daily-login
# - /double-up-reward
# - /weekly-rewards-status
# Along with their helper functions:
# - award_nonpremium_cosmetic
# - get_daily_reward_status 