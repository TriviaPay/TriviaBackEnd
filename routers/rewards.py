from fastapi import APIRouter, Depends, HTTPException, status, Body, Path, Query, Request
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
from rewards_logic import perform_draw
from scheduler import update_draw_scheduler

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
    status: str
    draw_date: date
    total_participants: int
    total_winners: int
    winners: List[WinnerResponse]
    prize_pool: float

class DrawConfigUpdateRequest(BaseModel):
    custom_winner_count: Optional[int] = Field(None, description="Custom number of winners")
    draw_time_hour: Optional[int] = Field(20, ge=0, le=23, description="Hour for daily draw (0-23)")
    draw_time_minute: Optional[int] = Field(0, ge=0, le=59, description="Minute for daily draw (0-59)")
    draw_timezone: Optional[str] = Field(None, description="Timezone for daily draw (e.g., 'US/Eastern')")
    is_custom: Optional[bool] = Field(None, description="Whether to use custom winner count")
    automatic_draws: Optional[bool] = Field(None, description="Whether draws happen automatically")

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
                status="no_participants",
                draw_date=target_date,
                total_participants=0,
                total_winners=0,
                winners=[],
                prize_pool=0
            )
        
        # Get draw configuration
        config = db.query(TriviaDrawConfig).order_by(TriviaDrawConfig.id.desc()).first()
        
        if not config:
            logging.warning("No draw configuration found, using defaults")
            config = TriviaDrawConfig(
                is_custom=False,
                custom_winner_count=None,
                daily_pool_amount=100.0, 
                daily_winners_count=3,
                automatic_draws=True,
                draw_time_hour=int(os.environ.get("DRAW_TIME_HOUR", "23")),
                draw_time_minute=int(os.environ.get("DRAW_TIME_MINUTE", "59")),
                draw_timezone=os.environ.get("DRAW_TIMEZONE", "EST"),
                use_dynamic_calculation=True
            )
            db.add(config)
            db.commit()
            db.refresh(config)
        
        # If we need to do a last-minute calculation
        if config.use_dynamic_calculation and (config.calculated_pool_amount is None or 
                                              config.calculated_winner_count is None or
                                              config.last_calculation_time is None or
                                              datetime.now() - config.last_calculation_time > timedelta(hours=12)):
            # Count subscribed users
            subscribed_users = db.query(func.count(User.account_id)).filter(
                User.subscription_flag == True
            ).scalar() or 0
            
            # Calculate winner count based on the predefined table
            winner_count = 3  # Default
            if subscribed_users >= 2000:
                winner_count = 53
            elif subscribed_users >= 1300:
                winner_count = 47
            elif subscribed_users >= 1200:
                winner_count = 43
            elif subscribed_users >= 1100:
                winner_count = 41
            elif subscribed_users >= 1000:
                winner_count = 37
            elif subscribed_users >= 900:
                winner_count = 31
            elif subscribed_users >= 800:
                winner_count = 29
            elif subscribed_users >= 700:
                winner_count = 23
            elif subscribed_users >= 600:
                winner_count = 19
            elif subscribed_users >= 500:
                winner_count = 17
            elif subscribed_users >= 400:
                winner_count = 13
            elif subscribed_users >= 300:
                winner_count = 11
            elif subscribed_users >= 200:
                winner_count = 7
            elif subscribed_users >= 100:
                winner_count = 5
            elif subscribed_users >= 50:
                winner_count = 3
            
            # Calculate prize pool:
            # Each subscriber contributes $5, with $0.70 platform fee, 
            # leaving $4.30 per user for the prize pool
            total_subscription_amount = subscribed_users * 5.0
            platform_fees = subscribed_users * 0.70
            available_amount = total_subscription_amount - platform_fees
            
            # If more than 200 subscribers, add 18% revenue cut
            revenue_cut = 0
            if subscribed_users > 200:
                revenue_cut = available_amount * 0.18
                available_amount -= revenue_cut
            
            # Calculate daily amount by dividing by days in current month
            days_in_month = calendar.monthrange(datetime.now().year, datetime.now().month)[1]
            daily_pool_amount = round(available_amount / days_in_month, 2)
            
            # Update config
            config.calculated_winner_count = winner_count
            config.calculated_pool_amount = daily_pool_amount
            config.last_calculation_time = datetime.now()
            db.commit()
            
            logging.info(f"Updated calculated values: winners={winner_count}, pool=${daily_pool_amount}")
        
        # Determine effective winner count and pool amount
        effective_winner_count = None
        effective_pool_amount = None
        
        if config.is_custom and config.custom_winner_count is not None:
            effective_winner_count = config.custom_winner_count
            logging.info(f"Using custom winner count: {effective_winner_count}")
        elif config.calculated_winner_count is not None:
            effective_winner_count = config.calculated_winner_count
            logging.info(f"Using calculated winner count: {effective_winner_count}")
        else:
            effective_winner_count = config.daily_winners_count
            logging.info(f"Using default winner count: {effective_winner_count}")
            
        if config.is_custom and config.daily_pool_amount is not None:
            effective_pool_amount = config.daily_pool_amount
            logging.info(f"Using custom pool amount: {effective_pool_amount}")
        elif config.calculated_pool_amount is not None:
            effective_pool_amount = config.calculated_pool_amount
            logging.info(f"Using calculated pool amount: {effective_pool_amount}")
        else:
            effective_pool_amount = 100.0
            logging.info(f"Using default pool amount: {effective_pool_amount}")
        
        # Cap winner count by participant count
        actual_winner_count = min(effective_winner_count, participant_count)
        logging.info(f"Final winner count: {actual_winner_count}")
        
        # Calculate prize distribution
        prizes = calculate_prize_distribution(effective_pool_amount, actual_winner_count)
        logging.info(f"Prize distribution: {prizes}")
        
        # Select winners randomly
        if participant_count <= actual_winner_count:
            # If there are fewer participants than winners, everyone wins
            winners = eligible_users
            logging.info("All participants selected as winners (fewer participants than winner slots)")
        else:
            # Otherwise, randomly select winners
            winners = random.sample(eligible_users, actual_winner_count)
            logging.info(f"Randomly selected {actual_winner_count} winners from {participant_count} participants")
        
        # Save winners to database
        winner_responses = []
        
        for i, winner in enumerate(winners):
            if i < len(prizes):
                prize_amount = prizes[i]
                logging.info(f"Processing winner {i+1}: User {winner.account_id} ({winner.username}), prize: ${prize_amount}")
                
                # Save to database
                db_winner = TriviaDrawWinner(
                    account_id=winner.account_id,
                    prize_amount=prize_amount,
                    position=i+1,
                    draw_date=target_date,
                    draw_type='daily',
                    created_at=datetime.now()
                )
                db.add(db_winner)
                
                # Update user's wallet balance
                winner.wallet_balance = (winner.wallet_balance or 0) + prize_amount
                winner.last_wallet_update = datetime.utcnow()
                
                # Get winner details
                badge_name = None
                badge_image_url = None
                if hasattr(winner, 'badge_info') and winner.badge_info:
                    badge_name = winner.badge_info.name
                    badge_image_url = winner.badge_image_url
                
                # Get avatar URL
                avatar_url = None
                if hasattr(winner, 'selected_avatar_id') and winner.selected_avatar_id:
                    avatar = db.query(Avatar).filter(Avatar.id == winner.selected_avatar_id).first()
                    if avatar:
                        avatar_url = avatar.image_url
                
                # Get frame URL
                frame_url = None
                if hasattr(winner, 'selected_frame_id') and winner.selected_frame_id:
                    frame = db.query(Frame).filter(Frame.id == winner.selected_frame_id).first()
                    if frame:
                        frame_url = frame.image_url
                
                # Calculate total amount won by this user
                total_won = db.query(func.sum(TriviaDrawWinner.prize_amount)).filter(
                    TriviaDrawWinner.account_id == winner.account_id
                ).scalar() or 0
                
                # Add to response
                winner_responses.append(WinnerResponse(
                    username=winner.username or f"User{winner.account_id}",
                    amount_won=prize_amount,
                    total_amount_won=total_won + prize_amount,  # Include today's prize
                    badge_name=badge_name,
                    badge_image_url=badge_image_url,
                    avatar_url=avatar_url,
                    frame_url=frame_url,
                    position=i+1,
                    draw_date=target_date.isoformat()
                ))
        
        # Commit all winners to database
        db.commit()
        
        # Return response
        return DrawResponse(
            status="success",
            draw_date=target_date,
            total_participants=participant_count,
            total_winners=actual_winner_count,
            winners=winner_responses,
            prize_pool=effective_pool_amount
        )
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error triggering draw: {str(e)}", exc_info=True)
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
    Admin endpoint to reset the draw winner logic to default calculation.
    This sets is_custom to False and clears all custom values.
    """
    try:
        logging.info("Resetting winner logic to defaults")
        
        # Get current configuration or create new one
        config = db.query(TriviaDrawConfig).order_by(TriviaDrawConfig.id.desc()).first()
        
        if not config:
            # Create default configuration
            config = TriviaDrawConfig(
                is_custom=False,
                custom_winner_count=None,
                daily_pool_amount=None,
                daily_winners_count=3,
                automatic_draws=True,
                draw_time_hour=int(os.environ.get("DRAW_TIME_HOUR", "20")),
                draw_time_minute=int(os.environ.get("DRAW_TIME_MINUTE", "00")),
                draw_timezone=os.environ.get("DRAW_TIMEZONE", "EST"),
                use_dynamic_calculation=True,
                calculated_pool_amount=None,
                calculated_winner_count=None,
                last_calculation_time=None
            )
            db.add(config)
        else:
            # Reset the existing configuration
            config.is_custom = False
            config.custom_winner_count = None
            config.daily_pool_amount = None
            config.use_dynamic_calculation = True
            
            # Force recalculation
            config.calculated_pool_amount = None
            config.calculated_winner_count = None
            config.last_calculation_time = None
            
            # Keep draw time settings but make sure they have values
            if config.draw_time_hour is None:
                config.draw_time_hour = int(os.environ.get("DRAW_TIME_HOUR", "20"))
            if config.draw_time_minute is None:
                config.draw_time_minute = int(os.environ.get("DRAW_TIME_MINUTE", "00"))
            if config.draw_timezone is None or not config.draw_timezone:
                config.draw_timezone = os.environ.get("DRAW_TIMEZONE", "EST")
            
        db.commit()
        db.refresh(config)
        
        logging.info("Successfully reset winner logic to defaults")
        
        # Return the updated configuration
        return DrawConfigResponse(
            is_custom=config.is_custom,
            custom_winner_count=config.custom_winner_count,
            draw_time_hour=config.draw_time_hour,
            draw_time_minute=config.draw_time_minute,
            draw_timezone=config.draw_timezone,
            custom_data=None
        )
    
    except Exception as e:
        logging.error(f"Error resetting winner logic: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error resetting winner logic: {str(e)}"
        )

@router.put("/update-draw-config", response_model=dict)
async def update_draw_config(
    request: Request,
    req_config: DrawConfigUpdateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Update the draw configuration in the database.
    Simplified to only allow updating custom_winner_count, draw_time_hour, and draw_time_minute.
    """
    try:
        logger.info(f"--- Entering update_draw_config with payload: {req_config.dict(exclude_unset=True)} ---")
        
        # Check for multiple config rows (potential issue indicator)
        config_count = db.query(func.count(TriviaDrawConfig.id)).scalar()
        if config_count > 1:
            logger.warning(f"Multiple ({config_count}) rows found in trivia_draw_config table. Only the latest (by ID) will be updated.")

        # Get the latest config record to update, or create if none exists
        db_config = db.query(TriviaDrawConfig).order_by(TriviaDrawConfig.id.desc()).first()

        if not db_config:
            logger.info("No existing config found. Creating new one.")
            db_config = TriviaDrawConfig(
                is_custom=False,
                custom_winner_count=None,
                daily_pool_amount=0.0,
                daily_winners_count=1,
                automatic_draws=True,
                draw_time_hour=20,
                draw_time_minute=0,
                draw_timezone="US/Eastern",
                use_dynamic_calculation=True
            )
            db.add(db_config)
        
        # --- Prepare updates ---
        updated_fields = []
        
        # Update custom winner count if provided
        if req_config.custom_winner_count is not None:
            db_config.custom_winner_count = req_config.custom_winner_count
            db_config.is_custom = True  # Automatically set is_custom to True
            updated_fields.append(f"custom_winner_count={req_config.custom_winner_count}")
            updated_fields.append("is_custom=True")
        
        # Update draw time hour if provided
        if req_config.draw_time_hour is not None:
            db_config.draw_time_hour = req_config.draw_time_hour
            updated_fields.append(f"draw_time_hour={req_config.draw_time_hour}")
        
        # Update draw time minute if provided
        if req_config.draw_time_minute is not None:
            db_config.draw_time_minute = req_config.draw_time_minute
            updated_fields.append(f"draw_time_minute={req_config.draw_time_minute}")
        
        db.commit()
        db.refresh(db_config)
        
        # Update the scheduler if the draw time was changed
        if (req_config.draw_time_hour is not None or 
            req_config.draw_time_minute is not None):
            
            # Update the scheduler
            update_success = update_draw_scheduler()
            if not update_success:
                logger.warning("Failed to update draw scheduler with new configuration")
        
        if updated_fields:
            logger.info(f"Updated fields: {', '.join(updated_fields)}")
        else:
            logger.info("No fields were updated (values unchanged)")
            
        # Return the updated configuration
        return get_draw_config(request, db, current_user)
        
    except Exception as e:
        logger.error(f"Error updating draw configuration: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating draw configuration: {str(e)}"
        )

# ======== Daily Rewards Endpoints ========

# These endpoints have been moved to routers/daily_rewards.py
# - /daily-login
# - /double-up-reward
# - /weekly-rewards-status
# Along with their helper functions:
# - award_nonpremium_cosmetic
# - get_daily_reward_status 