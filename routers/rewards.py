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
from models import User, TriviaDrawWinner, TriviaDrawConfig, DailyQuestion, Trivia, Badge, Avatar, Frame, Entry
from routers.dependencies import get_current_user, get_admin_user
from sqlalchemy.sql import extract
import os
import json
import logging

router = APIRouter(tags=["Rewards"])

# ======== Models ========

class WinnerResponse(BaseModel):
    username: str
    amount_won: float
    total_amount_won: float = 0
    badge_name: str = None
    badge_image_url: str = None
    avatar_url: str = None
    frame_url: str = None
    position: int

class DrawConfigResponse(BaseModel):
    is_custom: bool
    custom_winner_count: Optional[int] = None
    draw_time_hour: int = 20
    draw_time_minute: int = 0
    draw_timezone: str = "US/Eastern"

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

def get_eligible_users(db: Session, draw_date: date) -> List[User]:
    """Get list of eligible users for the draw."""
    # Log the requested draw date for debugging
    logging.info(f"Getting eligible users for draw date: {draw_date}")
    
    # Get all users for testing
    all_users = db.query(User).all()
    logging.info(f"Total users found: {len(all_users)}")
    
    # Get subscribed users
    subscribed_users = db.query(User).filter(User.subscription_flag == True).all()
    logging.info(f"Users with subscription: {len(subscribed_users)}")
    
    eligible_user_ids = []
    
    # METHOD 1: Check which users answered correctly in the DailyQuestion table
    for user in all_users:  # Test with all users first
        # Log which user we're checking
        logging.info(f"Checking eligibility for user {user.account_id} ({user.username or 'unknown'})")
        
        # Get the start and end of the draw date for comparison
        start_date = datetime.combine(draw_date, datetime.min.time())
        end_date = datetime.combine(draw_date, datetime.max.time())
        
        # Get daily questions for this user that were answered correctly on the draw date
        correct_answers = db.query(DailyQuestion).filter(
            DailyQuestion.account_id == user.account_id,
            DailyQuestion.date.between(start_date, end_date),
            DailyQuestion.is_used == True,
            DailyQuestion.is_correct == True
        ).count()
        
        logging.info(f"Method 1: User {user.account_id} has {correct_answers} correct answers in DailyQuestion on {draw_date}")
        
        # METHOD 2: Check the Entry table as an alternative
        entry = db.query(Entry).filter(
            Entry.account_id == user.account_id,
            Entry.date == draw_date,
            Entry.correct_answers > 0
        ).first()
        
        entry_correct = entry is not None and entry.correct_answers > 0
        logging.info(f"Method 2: User {user.account_id} has Entry with correct_answers > 0: {entry_correct}")
        
        # User is eligible if either method shows they answered correctly
        if correct_answers > 0 or entry_correct:
            eligible_user_ids.append(user.account_id)
            logging.info(f"User {user.account_id} is eligible for the draw")
    
    # Return list of eligible users
    eligible_users = db.query(User).filter(User.account_id.in_(eligible_user_ids)).all()
    logging.info(f"Found {len(eligible_users)} eligible users for the draw")
    
    # FOR TESTING: If no eligible users found, just return a few random users
    if not eligible_users and all_users:
        test_users = all_users[:min(3, len(all_users))]
        logging.warning(f"No eligible users found. Returning {len(test_users)} test users for debugging.")
        return test_users
    
    return eligible_users

# ======== API Endpoints ========

@router.get("/daily-winners", response_model=List[WinnerResponse])
async def get_daily_winners(
    date_str: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Get the list of daily winners for a specific date.
    If no date is provided, returns today's winners.
    """
    try:
        logging.info(f"get_daily_winners called with date_str={date_str}")
        
        if date_str:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        else:
            # Default to yesterday if no date specified
            est = pytz.timezone('US/Eastern')
            target_date = (datetime.now(est) - timedelta(days=1)).date()

        logging.info(f"Target date for winners: {target_date}")

        # Get winners for the specified date
        winners_query = db.query(TriviaDrawWinner, User).join(
            User, TriviaDrawWinner.account_id == User.account_id
        ).filter(
            TriviaDrawWinner.draw_date == target_date
        ).order_by(TriviaDrawWinner.position).all()
        
        logging.info(f"Found {len(winners_query)} winners for date {target_date}")

        result = []
        
        for winner, user in winners_query:
            try:
                # Calculate total amount won by user all-time
                total_won = db.query(func.sum(TriviaDrawWinner.prize_amount)).filter(
                    TriviaDrawWinner.account_id == user.account_id
                ).scalar() or 0
                
                # Get badge information
                badge_name = None
                badge_image_url = None
                if user.badge_info:
                    badge_name = user.badge_info.name
                    badge_image_url = user.badge_image_url
                
                # Get avatar URL
                avatar_url = None
                if user.selected_avatar_id:
                    avatar_query = text("""
                        SELECT image_url FROM avatars 
                        WHERE id = :avatar_id
                    """)
                    avatar_result = db.execute(avatar_query, {"avatar_id": user.selected_avatar_id}).first()
                    if avatar_result:
                        avatar_url = avatar_result[0]
                
                # Get frame URL
                frame_url = None
                if user.selected_frame_id:
                    frame_query = text("""
                        SELECT image_url FROM frames 
                        WHERE id = :frame_id
                    """)
                    frame_result = db.execute(frame_query, {"frame_id": user.selected_frame_id}).first()
                    if frame_result:
                        frame_url = frame_result[0]
                
                result.append(WinnerResponse(
                    username=user.username or f"User{user.account_id}",
                    amount_won=winner.prize_amount,
                    total_amount_won=total_won,
                    badge_name=badge_name,
                    badge_image_url=badge_image_url,
                    avatar_url=avatar_url,
                    frame_url=frame_url,
                    position=winner.position
                ))
            except Exception as user_error:
                logging.error(f"Error processing winner {user.account_id}: {str(user_error)}")
        
        return result
        
    except Exception as e:
        logging.error(f"Error in get_daily_winners: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving daily winners: {str(e)}"
        )

@router.get("/weekly-winners", response_model=List[WinnerResponse])
async def get_weekly_winners(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Get the list of top winners for the current week (Monday to Sunday).
    Returns users who won the most in the current week.
    """
    try:
        # Get current date in EST
        est = pytz.timezone('US/Eastern')
        today = datetime.now(est).date()
        
        # Calculate start of the week (Monday)
        start_of_week = today - timedelta(days=today.weekday())
        
        # Calculate end of the week (Sunday)
        end_of_week = start_of_week + timedelta(days=6)
        
        # Get weekly winners (aggregated by account_id)
        winners_query = text("""
            SELECT 
                dw.account_id, 
                SUM(dw.prize_amount) as weekly_amount,
                MIN(dw.position) as best_position
            FROM trivia_draw_winners dw
            WHERE dw.draw_date BETWEEN :start_date AND :end_date
            GROUP BY dw.account_id
            ORDER BY weekly_amount DESC
            LIMIT 50
        """)
        
        winners_result = db.execute(winners_query, {
            "start_date": start_of_week,
            "end_date": end_of_week
        }).fetchall()
        
        # Format the response
        result = []
        position = 1
        
        for winner_data in winners_result:
            account_id, weekly_amount, best_position = winner_data
            
            # Get user details
            user = db.query(User).filter(User.account_id == account_id).first()
            if not user:
                continue
            
            # Calculate total amount won by user all-time
            total_won = db.query(func.sum(TriviaDrawWinner.prize_amount)).filter(
                TriviaDrawWinner.account_id == user.account_id
            ).scalar() or 0
            
            # Get badge information
            badge_name = None
            badge_image_url = None
            if user.badge_info:
                badge_name = user.badge_info.name
                badge_image_url = user.badge_image_url
            
            # Get avatar URL
            avatar_url = None
            if user.selected_avatar_id:
                avatar_query = text("""
                    SELECT image_url FROM avatars 
                    WHERE id = :avatar_id
                """)
                avatar_result = db.execute(avatar_query, {"avatar_id": user.selected_avatar_id}).first()
                if avatar_result:
                    avatar_url = avatar_result[0]
            
            # Get frame URL
            frame_url = None
            if user.selected_frame_id:
                frame_query = text("""
                    SELECT image_url FROM frames 
                    WHERE id = :frame_id
                """)
                frame_result = db.execute(frame_query, {"frame_id": user.selected_frame_id}).first()
                if frame_result:
                    frame_url = frame_result[0]
            
            result.append(WinnerResponse(
                username=user.username or f"User{user.account_id}",
                amount_won=weekly_amount,
                total_amount_won=total_won,
                badge_name=badge_name,
                badge_image_url=badge_image_url,
                avatar_url=avatar_url,
                frame_url=frame_url,
                position=position
            ))
            
            position += 1
        
        return result
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving weekly winners: {str(e)}"
        )

@router.get("/all-time-winners", response_model=List[WinnerResponse])
async def get_all_time_winners(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Get the list of all-time top winners.
    Returns users who won the most all-time.
    """
    try:
        # Get all-time winners (aggregated by account_id)
        winners_query = text("""
            SELECT 
                dw.account_id, 
                SUM(dw.prize_amount) as total_amount,
                MIN(dw.position) as best_position
            FROM trivia_draw_winners dw
            GROUP BY dw.account_id
            ORDER BY total_amount DESC
            LIMIT 50
        """)
        
        winners_result = db.execute(winners_query).fetchall()
        
        # Format the response
        result = []
        position = 1
        
        for winner_data in winners_result:
            account_id, total_amount, best_position = winner_data
            
            # Get user details
            user = db.query(User).filter(User.account_id == account_id).first()
            if not user:
                continue
            
            # Get badge information
            badge_name = None
            badge_image_url = None
            if user.badge_info:
                badge_name = user.badge_info.name
                badge_image_url = user.badge_image_url
            
            # Get avatar URL
            avatar_url = None
            if user.selected_avatar_id:
                avatar_query = text("""
                    SELECT image_url FROM avatars 
                    WHERE id = :avatar_id
                """)
                avatar_result = db.execute(avatar_query, {"avatar_id": user.selected_avatar_id}).first()
                if avatar_result:
                    avatar_url = avatar_result[0]
            
            # Get frame URL
            frame_url = None
            if user.selected_frame_id:
                frame_query = text("""
                    SELECT image_url FROM frames 
                    WHERE id = :frame_id
                """)
                frame_result = db.execute(frame_query, {"frame_id": user.selected_frame_id}).first()
                if frame_result:
                    frame_url = frame_result[0]
            
            result.append(WinnerResponse(
                username=user.username or f"User{user.account_id}",
                amount_won=total_amount,
                total_amount_won=total_amount,  # Same value for all-time
                badge_name=badge_name,
                badge_image_url=badge_image_url,
                avatar_url=avatar_url,
                frame_url=frame_url,
                position=position
            ))
            
            position += 1
        
        return result
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving all-time winners: {str(e)}"
        )

# ======== Admin Endpoints ========

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

@router.put("/admin/custom-winner-count", response_model=DrawConfigResponse)
async def set_custom_winner_count(
    winner_count: int = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to set a custom number of winners.
    This is an alternative to /admin/draw-config which only updates the winner count.
    """
    try:
        logging.info(f"Setting custom winner count to {winner_count}")
        
        if winner_count <= 0:
            logging.warning(f"Invalid winner count: {winner_count}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Winner count must be a positive number"
            )
        
        # Get or create config - ONLY use TriviaDrawConfig, not DrawConfig
        config = db.query(TriviaDrawConfig).first()
        logging.info(f"Current config: {config}")
        
        if not config:
            config = TriviaDrawConfig(
                is_custom=True,
                custom_winner_count=winner_count
            )
            db.add(config)
            logging.info(f"Created new config with custom_winner_count={winner_count}")
        else:
            config.is_custom = True
            config.custom_winner_count = winner_count
            logging.info(f"Updated existing config with custom_winner_count={winner_count}")
        
        db.commit()
        
        # Verify the config was saved correctly
        updated_config = db.query(TriviaDrawConfig).first()
        logging.info(f"Config after update: is_custom={updated_config.is_custom}, custom_winner_count={updated_config.custom_winner_count}")
        
        # Get draw time from environment for the response
        draw_time_hour = int(os.environ.get("DRAW_TIME_HOUR", "20"))
        draw_time_minute = int(os.environ.get("DRAW_TIME_MINUTE", "0"))
        draw_timezone = os.environ.get("DRAW_TIMEZONE", "US/Eastern")
        
        return DrawConfigResponse(
            is_custom=config.is_custom,
            custom_winner_count=config.custom_winner_count,
            draw_time_hour=draw_time_hour,
            draw_time_minute=draw_time_minute,
            draw_timezone=draw_timezone
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Error setting custom winner count: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error setting custom winner count: {str(e)}"
        )

@router.put("/admin/reset-winner-logic", response_model=DrawConfigResponse)
async def reset_winner_logic(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to reset to the default winner logic.
    """
    try:
        # Get or create config
        config = db.query(TriviaDrawConfig).first()
        if not config:
            config = TriviaDrawConfig(
                is_custom=False,
                custom_winner_count=None
            )
            db.add(config)
        else:
            config.is_custom = False
            config.custom_winner_count = None
        
        db.commit()
        
        # Get draw time from environment for the response
        draw_time_hour = int(os.environ.get("DRAW_TIME_HOUR", "20"))
        draw_time_minute = int(os.environ.get("DRAW_TIME_MINUTE", "0"))
        draw_timezone = os.environ.get("DRAW_TIMEZONE", "US/Eastern")

        return DrawConfigResponse(
            is_custom=config.is_custom,
            custom_winner_count=config.custom_winner_count,
            draw_time_hour=draw_time_hour,
            draw_time_minute=draw_time_minute,
            draw_timezone=draw_timezone
        )
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error resetting winner logic: {str(e)}"
        )

@router.get("/admin/config", response_model=DrawConfigResponse)
async def get_draw_config(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to get the current draw configuration.
    Kept for backwards compatibility - prefer using /admin/draw-config instead.
    """
    try:
        # ONLY use TriviaDrawConfig, not DrawConfig
        config = db.query(TriviaDrawConfig).first()
        logging.info(f"Getting draw config: {config}")
        
        if not config:
            config = TriviaDrawConfig(
                is_custom=False,
                custom_winner_count=None
            )
            db.add(config)
            db.commit()
            logging.info(f"Created new default config")
        
        logging.info(f"Current config: is_custom={config.is_custom}, custom_winner_count={config.custom_winner_count}")
        
        # Get draw time from environment for the response
        draw_time_hour = int(os.environ.get("DRAW_TIME_HOUR", "20"))
        draw_time_minute = int(os.environ.get("DRAW_TIME_MINUTE", "0"))
        draw_timezone = os.environ.get("DRAW_TIMEZONE", "US/Eastern")
        
        return DrawConfigResponse(
            is_custom=config.is_custom,
            custom_winner_count=config.custom_winner_count,
            draw_time_hour=draw_time_hour,
            draw_time_minute=draw_time_minute,
            draw_timezone=draw_timezone
        )
        
    except Exception as e:
        logging.error(f"Error getting draw configuration: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting draw configuration: {str(e)}"
        )

@router.put("/admin/draw-config")
async def update_draw_config(
    config: DrawConfigUpdateRequest,
    claims: dict = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    Update the draw configuration (admin only).
    """
    # Validate timezone if provided
    if config.draw_timezone:
        try:
            pytz.timezone(config.draw_timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            raise HTTPException(status_code=400, detail=f"Invalid timezone: {config.draw_timezone}")
    
    # Update environment variables
    updated_config = {}
    
    if config.draw_time_hour is not None:
        os.environ["DRAW_TIME_HOUR"] = str(config.draw_time_hour)
        updated_config["draw_time_hour"] = config.draw_time_hour
        
    if config.draw_time_minute is not None:
        os.environ["DRAW_TIME_MINUTE"] = str(config.draw_time_minute)
        updated_config["draw_time_minute"] = config.draw_time_minute
        
    if config.draw_timezone:
        os.environ["DRAW_TIMEZONE"] = config.draw_timezone
        updated_config["draw_timezone"] = config.draw_timezone
    
    # Also store the config in the database for persistence
    draw_config = db.query(TriviaDrawConfig).first()
    if not draw_config:
        draw_config = TriviaDrawConfig(
            is_custom=True,
            custom_winner_count=None
        )
        db.add(draw_config)
    
    # Store the draw time in the custom_data field
    custom_data = {
        "draw_time_hour": int(os.environ.get("DRAW_TIME_HOUR", "20")),
        "draw_time_minute": int(os.environ.get("DRAW_TIME_MINUTE", "0")),
        "draw_timezone": os.environ.get("DRAW_TIMEZONE", "US/Eastern")
    }
    
    if hasattr(draw_config, 'custom_data'):
        # If the field exists, update it
        draw_config.custom_data = json.dumps(custom_data)
    else:
        # If the field doesn't exist yet, we'll need to add it via Alembic migration
        # For now, just log the issue
        logging.warning("custom_data field not available in TriviaDrawConfig model. Cannot store draw time in database.")
    
    db.commit()
    
    # Get current config for response
    current_config = {
        "draw_time_hour": int(os.environ.get("DRAW_TIME_HOUR", "20")),
        "draw_time_minute": int(os.environ.get("DRAW_TIME_MINUTE", "0")),
        "draw_timezone": os.environ.get("DRAW_TIMEZONE", "US/Eastern"),
        "updated_fields": list(updated_config.keys())
    }
    
    return current_config 