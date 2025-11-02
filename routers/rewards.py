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
from models import User, TriviaQuestionsWinners, TriviaDrawConfig, CompanyRevenue, TriviaQuestionsDaily, Trivia, Badge, Avatar, Frame, TriviaQuestionsEntries
from routers.dependencies import get_current_user, get_admin_user
from utils.storage import presign_get
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
    
    badge_image_url: Optional[str] = None
    avatar_url: Optional[str] = None
    frame_url: Optional[str] = None
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

# Import unified functions from rewards_logic
from rewards_logic import (
    calculate_winner_count, 
    calculate_prize_distribution, 
    calculate_prize_pool,
    get_eligible_participants,
    reset_daily_eligibility_flags,
    reset_monthly_subscriptions,
    update_user_eligibility
)

# ======== Helper Functions ========

# All helper functions are now imported from rewards_logic.py for consistency

def get_eligible_users_wrapper(db: Session, draw_date: date) -> List[User]:
    """
    Wrapper function that converts the unified get_eligible_participants 
    to return User objects for compatibility with admin endpoints.
    """
    participants = get_eligible_participants(db, draw_date)
    
    if not participants:
        return []
    
    # Extract account IDs and query User objects
    account_ids = [p["account_id"] for p in participants]
    eligible_users = db.query(User).filter(User.account_id.in_(account_ids)).all()
    
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
        if date_str:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        else:
            # Default to yesterday if no date specified
            est = pytz.timezone('US/Eastern')
            target_date = (datetime.now(est) - timedelta(days=1)).date()

        # Get winners for the specified date
        winners = db.query(TriviaQuestionsWinners, User).join(
            User, TriviaQuestionsWinners.account_id == User.account_id
        ).filter(
            TriviaQuestionsWinners.draw_date == target_date
        ).order_by(TriviaQuestionsWinners.position).all()

        result = []
        
        for winner, user in winners:
            # Calculate total amount won by user all-time
            total_won = db.query(func.sum(TriviaQuestionsWinners.prize_amount)).filter(
                TriviaQuestionsWinners.account_id == user.account_id
            ).scalar() or 0
            
            # Get badge information
            # Note: Badge URLs are public S3 URLs (not presigned), so return directly
            badge_image_url = None
            if user.badge_info:
                badge_image_url = user.badge_image_url  # Public URL, no presigning needed
            
            # Get avatar URL (presigned)
            avatar_url = None
            if user.selected_avatar_id:
                avatar_obj = db.query(Avatar).filter(Avatar.id == user.selected_avatar_id).first()
                if avatar_obj:
                    bucket = getattr(avatar_obj, "bucket", None)
                    object_key = getattr(avatar_obj, "object_key", None)
                    if bucket and object_key:
                        try:
                            avatar_url = presign_get(bucket, object_key, expires=900)
                        except Exception as e:
                            logging.warning(f"Failed to presign avatar {avatar_obj.id}: {e}")
            
            # Get frame URL (presigned)
            frame_url = None
            if user.selected_frame_id:
                frame_obj = db.query(Frame).filter(Frame.id == user.selected_frame_id).first()
                if frame_obj:
                    bucket = getattr(frame_obj, "bucket", None)
                    object_key = getattr(frame_obj, "object_key", None)
                    if bucket and object_key:
                        try:
                            frame_url = presign_get(bucket, object_key, expires=900)
                        except Exception as e:
                            logging.warning(f"Failed to presign frame {frame_obj.id}: {e}")
            
            result.append(WinnerResponse(
                username=user.username or f"User{user.account_id}",
                amount_won=winner.prize_amount,
                total_amount_won=total_won,
                
                badge_image_url=badge_image_url,
                avatar_url=avatar_url,
                frame_url=frame_url,
                position=winner.position
            ))
        
        return result
        
    except Exception as e:
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
            FROM winners_draw_results dw
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
            total_won = db.query(func.sum(TriviaQuestionsWinners.prize_amount)).filter(
                TriviaQuestionsWinners.account_id == user.account_id
            ).scalar() or 0
            
            # Get badge information
            # Note: Badge URLs are public S3 URLs (not presigned), so return directly
            badge_image_url = None
            if user.badge_info:
                badge_image_url = user.badge_image_url  # Public URL, no presigning needed
            
            # Get avatar URL (presigned)
            avatar_url = None
            if user.selected_avatar_id:
                avatar_obj = db.query(Avatar).filter(Avatar.id == user.selected_avatar_id).first()
                if avatar_obj:
                    bucket = getattr(avatar_obj, "bucket", None)
                    object_key = getattr(avatar_obj, "object_key", None)
                    if bucket and object_key:
                        try:
                            avatar_url = presign_get(bucket, object_key, expires=900)
                        except Exception as e:
                            logging.warning(f"Failed to presign avatar {avatar_obj.id}: {e}")
            
            # Get frame URL (presigned)
            frame_url = None
            if user.selected_frame_id:
                frame_obj = db.query(Frame).filter(Frame.id == user.selected_frame_id).first()
                if frame_obj:
                    bucket = getattr(frame_obj, "bucket", None)
                    object_key = getattr(frame_obj, "object_key", None)
                    if bucket and object_key:
                        try:
                            frame_url = presign_get(bucket, object_key, expires=900)
                        except Exception as e:
                            logging.warning(f"Failed to presign frame {frame_obj.id}: {e}")
            
            result.append(WinnerResponse(
                username=user.username or f"User{user.account_id}",
                amount_won=weekly_amount,
                total_amount_won=total_won,
                
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
            FROM winners_draw_results dw
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
            # Note: Badge URLs are public S3 URLs (not presigned), so return directly
            badge_image_url = None
            if user.badge_info:
                badge_image_url = user.badge_image_url  # Public URL, no presigning needed
            
            # Get avatar URL (presigned)
            avatar_url = None
            if user.selected_avatar_id:
                avatar_obj = db.query(Avatar).filter(Avatar.id == user.selected_avatar_id).first()
                if avatar_obj:
                    bucket = getattr(avatar_obj, "bucket", None)
                    object_key = getattr(avatar_obj, "object_key", None)
                    if bucket and object_key:
                        try:
                            avatar_url = presign_get(bucket, object_key, expires=900)
                        except Exception as e:
                            logging.warning(f"Failed to presign avatar {avatar_obj.id}: {e}")
            
            # Get frame URL (presigned)
            frame_url = None
            if user.selected_frame_id:
                frame_obj = db.query(Frame).filter(Frame.id == user.selected_frame_id).first()
                if frame_obj:
                    bucket = getattr(frame_obj, "bucket", None)
                    object_key = getattr(frame_obj, "object_key", None)
                    if bucket and object_key:
                        try:
                            frame_url = presign_get(bucket, object_key, expires=900)
                        except Exception as e:
                            logging.warning(f"Failed to presign frame {frame_obj.id}: {e}")
            
            result.append(WinnerResponse(
                username=user.username or f"User{user.account_id}",
                amount_won=total_amount,
                total_amount_won=total_amount,  # Same value for all-time
                
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

@router.get("/reveal-winners")
async def reveal_winners(
    date_str: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Reveal profile pictures of all participants in a draw.
    Returns only profile picture URLs for eligible participants.
    
    Args:
        date_str: Optional date string in YYYY-MM-DD format. Defaults to today.
        
    Returns:
        List of profile picture URLs for all participants in the draw
    """
    try:
        # Determine the draw date
        if date_str:
            try:
                draw_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid date format. Use YYYY-MM-DD."
                )
        else:
            # Default to today's date
            draw_date = date.today()
        
        # Get eligible participants for the draw date
        participants = get_eligible_participants(db, draw_date)
        
        if not participants:
            return {
                "draw_date": draw_date.isoformat(),
                "total_participants": 0,
                "profile_pics": []
            }
        
        # Get account IDs of participants
        account_ids = [p["account_id"] for p in participants]
        
        # Query users to get their profile pictures
        eligible_users = db.query(User).filter(
            User.account_id.in_(account_ids)
        ).all()
        
        # Extract profile picture URLs (only non-null values)
        profile_pics = [
            user.profile_pic_url 
            for user in eligible_users 
            if user.profile_pic_url
        ]
        
        return {
            "draw_date": draw_date.isoformat(),
            "total_participants": len(participants),
            "profile_pics": profile_pics
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error revealing winners: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving participant profile pictures: {str(e)}"
        )

@router.get("/today-winners-reveal")
async def today_winners_reveal(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Reveal today's winners with complete profile information.
    Returns username, profile pic, profile frame, avatar, prize amount, and badge for each winner.
    """
    try:
        # Get today's date
        today = date.today()
        
        # Get winners for today
        winners = db.query(TriviaQuestionsWinners, User).join(
            User, TriviaQuestionsWinners.account_id == User.account_id
        ).filter(
            TriviaQuestionsWinners.draw_date == today
        ).order_by(TriviaQuestionsWinners.position).all()
        
        if not winners:
            return {
                "draw_date": today.isoformat(),
                "total_winners": 0,
                "winners": []
            }
        
        result = []
        
        for winner, user in winners:
            # Get badge information (public URL, no presigning needed)
            badge_info = None
            if user.badge_id:
                badge = db.query(Badge).filter(Badge.id == user.badge_id).first()
                if badge:
                    badge_info = {
                        "id": badge.id,
                        "name": badge.name,
                        "image_url": badge.image_url  # Public URL
                    }
            
            # Get avatar URL (presigned)
            avatar_url = None
            if user.selected_avatar_id:
                avatar_obj = db.query(Avatar).filter(Avatar.id == user.selected_avatar_id).first()
                if avatar_obj:
                    bucket = getattr(avatar_obj, "bucket", None)
                    object_key = getattr(avatar_obj, "object_key", None)
                    if bucket and object_key:
                        try:
                            avatar_url = presign_get(bucket, object_key, expires=900)
                        except Exception as e:
                            logging.warning(f"Failed to presign avatar {avatar_obj.id}: {e}")
            
            # Get frame URL (presigned)
            frame_url = None
            if user.selected_frame_id:
                frame_obj = db.query(Frame).filter(Frame.id == user.selected_frame_id).first()
                if frame_obj:
                    bucket = getattr(frame_obj, "bucket", None)
                    object_key = getattr(frame_obj, "object_key", None)
                    if bucket and object_key:
                        try:
                            frame_url = presign_get(bucket, object_key, expires=900)
                        except Exception as e:
                            logging.warning(f"Failed to presign frame {frame_obj.id}: {e}")
            
            result.append({
                "username": user.username or f"User{user.account_id}",
                "profile_pic": user.profile_pic_url,
                "profile_frame": frame_url,  # Frame URL (presigned)
                "avatar": avatar_url,  # Avatar URL (presigned)
                "prize_amount": float(winner.prize_amount),
                "badge": badge_info  # Badge info (id, name, image_url) or None
            })
        
        return {
            "draw_date": today.isoformat(),
            "total_winners": len(result),
            "winners": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error revealing today's winners: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving today's winners: {str(e)}"
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
        existing_draw = db.query(TriviaQuestionsWinners).filter(
            TriviaQuestionsWinners.draw_date == target_date
        ).first()
        
        if existing_draw:
            logging.warning(f"Draw already performed for {target_date}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A draw has already been performed for {target_date}"
            )
        
        # Get eligible users using unified logic
        eligible_users = get_eligible_users_wrapper(db, target_date)
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
        
        # Use the unified perform_draw logic from rewards_logic
        from rewards_logic import perform_draw
        
        result = perform_draw(db, target_date)
        
        if result["status"] != "success":
            if result["status"] == "no_participants":
                return DrawResponse(
                    total_participants=0,
                    total_winners=0,
                    winners=[],
                    prize_pool=0
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=result.get("message", f"Draw failed with status: {result['status']}")
                )
        
        # Convert result to response format
        winner_responses = []
        for winner_data in result["winners"]:
            winner_responses.append(WinnerResponse(
                username=winner_data["username"] or "Unknown",
                amount_won=winner_data["prize_amount"],
                position=winner_data["position"]
            ))
        
        return DrawResponse(
            total_participants=result["total_participants"],
            total_winners=result["total_winners"],
            winners=winner_responses,
            prize_pool=result["prize_pool"]
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