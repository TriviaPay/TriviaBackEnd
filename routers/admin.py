import os
from datetime import date, datetime, time, timedelta
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Body, status, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import pytz
import logging
import json
from sqlalchemy import func # Import func for count

from db import get_db
from models import TriviaDrawConfig, TriviaDrawWinner, CompanyRevenue, Transaction, User, Avatar, UserAvatar, Frame, UserFrame, UserQuestionAnswer
from routers.dependencies import get_admin_user
from rewards_logic import perform_draw, get_daily_winners, get_weekly_winners
from scheduler import update_draw_scheduler

# Configure logging at the top level if not already done
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])

# Request models
class DrawConfigUpdateRequest(BaseModel):
    custom_winner_count: Optional[int] = Field(None, description="Custom number of winners")
    draw_time_hour: Optional[int] = Field(20, ge=0, le=23, description="Hour for daily draw (0-23)")
    draw_time_minute: Optional[int] = Field(0, ge=0, le=59, description="Minute for daily draw (0-59)")
    
    class Config:
        schema_extra = {
            "example": {
                "custom_winner_count": 1,
                "draw_time_hour": 20,
                "draw_time_minute": 0
            }
        }

# Response models
class DrawConfigResponse(BaseModel):
    is_custom: bool
    custom_winner_count: Optional[int] = None
    draw_time_hour: int
    draw_time_minute: int
    draw_timezone: str
    custom_data: Optional[Dict[str, Any]] = None

class DrawResponse(BaseModel):
    status: str
    draw_date: date
    total_participants: int
    total_winners: int
    prize_pool: float
    winners: List[Dict[str, Any]]

# Default values - read from env vars at startup or use hardcoded defaults
DEFAULT_DRAW_HOUR = int(os.environ.get("DRAW_TIME_HOUR", "20"))
DEFAULT_DRAW_MINUTE = int(os.environ.get("DRAW_TIME_MINUTE", "0"))
DEFAULT_TIMEZONE = os.environ.get("DRAW_TIMEZONE", "US/Eastern")

# This will be what Swagger UI shows
class DrawConfigWrapper(BaseModel):
    req_config: DrawConfigUpdateRequest

@router.get("/draw-config", response_model=dict)
async def get_admin_draw_config(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Get the current draw configuration
    
    This endpoint requires admin privileges.
    """
    logger.info("Admin accessing draw configuration")
    
    try:
        # Query for the current draw configuration
        draw_config = db.query(TriviaDrawConfig).order_by(TriviaDrawConfig.id.desc()).first()
        
        if not draw_config:
            # Create default configuration if none exists
            draw_config = TriviaDrawConfig(
                is_custom=False,
                custom_winner_count=None,
                daily_pool_amount=0.0,
                daily_winners_count=1,
                automatic_draws=True,
                draw_time_hour=int(os.environ.get("DRAW_TIME_HOUR", "20")),
                draw_time_minute=int(os.environ.get("DRAW_TIME_MINUTE", "0")),
                draw_timezone=os.environ.get("DRAW_TIMEZONE", "US/Eastern"),
                use_dynamic_calculation=True
            )
            db.add(draw_config)
            db.commit()
            db.refresh(draw_config)
            
            logger.info("Created default draw configuration")
            
        # Fetch the last daily draw timestamp
        last_daily_draw = db.query(TriviaDrawWinner).filter(
            TriviaDrawWinner.draw_type == 'daily'
        ).order_by(TriviaDrawWinner.created_at.desc()).first()
        
        # Count subscribed users
        subscribed_users_count = db.query(func.count(User.account_id)).filter(
            User.subscription_flag == True
        ).scalar() or 0
        
        # Count eligible participants (users who answered correctly today)
        today = datetime.now().date()
        eligible_users_count = db.query(func.count(User.account_id.distinct())).join(
            UserQuestionAnswer, 
            User.account_id == UserQuestionAnswer.account_id
        ).filter(
            UserQuestionAnswer.date == today,
            UserQuestionAnswer.is_correct == True
        ).scalar() or 0
        
        # Count subscribed eligible users
        subscribed_eligible_count = db.query(func.count(User.account_id.distinct())).join(
            UserQuestionAnswer, 
            User.account_id == UserQuestionAnswer.account_id
        ).filter(
            User.subscription_flag == True,
            UserQuestionAnswer.date == today,
            UserQuestionAnswer.is_correct == True
        ).scalar() or 0
        
        # Return response
        return {
            "config": {
                "is_custom": draw_config.is_custom,
                "custom_winner_count": draw_config.custom_winner_count,
                "daily_pool_amount": draw_config.daily_pool_amount,
                "daily_winners_count": draw_config.daily_winners_count,
                "automatic_draws": draw_config.automatic_draws,
                "draw_time_hour": draw_config.draw_time_hour,
                "draw_time_minute": draw_config.draw_time_minute,
                "draw_timezone": draw_config.draw_timezone,
                "use_dynamic_calculation": draw_config.use_dynamic_calculation,
                "calculated_pool_amount": draw_config.calculated_pool_amount,
                "calculated_winner_count": draw_config.calculated_winner_count,
                "last_calculation_time": draw_config.last_calculation_time
            },
            "last_draw_info": {
                "last_daily_draw": last_daily_draw.created_at if last_daily_draw else None
            },
            "participation_stats": {
                "total_subscribed_users": subscribed_users_count,
                "eligible_participants_today": eligible_users_count,
                "subscribed_eligible_participants": subscribed_eligible_count,
                "date": today.isoformat()
            }
        }

    except Exception as e:
        logger.error(f"Error getting draw configuration: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting draw configuration: {str(e)}"
        )

@router.put("/draw-config", response_model=dict)
async def update_draw_config(
    request: Request,
    req_config: Optional[DrawConfigUpdateRequest] = Body(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to update the draw configuration in the database.
    
    Request body format (either format works):
    ```json
    {
      "custom_winner_count": 6,
      "draw_time_hour": 20,
      "draw_time_minute": 0
    }
    ```
    Or:
    ```json
    {
      "req_config": {
        "custom_winner_count": 6,
        "draw_time_hour": 20,
        "draw_time_minute": 0
      }
    }
    ```
    """
    logger.info("Admin updating draw configuration")
    
    try:
        # Parse the request body to handle both formats
        json_data = await request.json()
        logger.info(f"Received request data: {json_data}")
        
        # Check if the request has a req_config wrapper
        if isinstance(json_data, dict) and "req_config" in json_data:
            config_data = json_data["req_config"]
            logger.info("Using nested req_config format")
        else:
            # Use the raw data directly
            config_data = json_data
            logger.info("Using direct format without req_config wrapper")
            
        # Convert to Pydantic model for validation
        try:
            req_config = DrawConfigUpdateRequest(**config_data)
        except Exception as e:
            logger.error(f"Validation error for request data: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid request data: {str(e)}"
            )
        
        # Get the current draw configuration
        draw_config = db.query(TriviaDrawConfig).order_by(TriviaDrawConfig.id.desc()).first()
        
        if not draw_config:
            # Create new configuration if none exists
            draw_config = TriviaDrawConfig(
                is_custom=False,
                custom_winner_count=None,
                daily_pool_amount=0.0,
                daily_winners_count=1,
                automatic_draws=True,
                draw_time_hour=DEFAULT_DRAW_HOUR,
                draw_time_minute=DEFAULT_DRAW_MINUTE,
                draw_timezone=DEFAULT_TIMEZONE,
                use_dynamic_calculation=True
            )
            db.add(draw_config)
        
        # Update configuration based on request
        if req_config.custom_winner_count is not None:
            draw_config.is_custom = True
            draw_config.custom_winner_count = req_config.custom_winner_count
        
        if req_config.draw_time_hour is not None:
            draw_config.draw_time_hour = req_config.draw_time_hour
        
        if req_config.draw_time_minute is not None:
            draw_config.draw_time_minute = req_config.draw_time_minute
        
        # Save changes
        db.commit()
        db.refresh(draw_config)
        
        # Update the scheduler with new draw time
        update_draw_scheduler()
        
        # Return updated configuration
        return {
            "status": "success",
            "message": "Draw configuration updated successfully",
            "config": {
                "is_custom": draw_config.is_custom,
                "custom_winner_count": draw_config.custom_winner_count,
                "draw_time_hour": draw_config.draw_time_hour,
                "draw_time_minute": draw_config.draw_time_minute,
                "draw_timezone": draw_config.draw_timezone
            }
        }
        
    except Exception as e:
        logger.error(f"Error updating draw configuration: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating draw configuration: {str(e)}"
        )

@router.post("/trigger-draw", response_model=dict, status_code=200, response_description="Daily draw triggered successfully")
async def trigger_draw(
    request: Request,
    db: Session = Depends(get_db),
    admin_user: dict = Depends(get_admin_user)
):
    """
    Trigger a daily draw manually
    
    This endpoint requires admin privileges.
    
    No request body is needed. Just send a POST request with the Authorization header.
    
    Returns:
        dict: {
            "success": bool,
            "message": str,
            "result": dict,
            "winners": list
        }
    """
    try:
        logger.info("Admin triggering daily draw manually")

        # Call the perform_draw function with today's date
        result = perform_draw(db)
        
        # Get daily winners
        winners = get_daily_winners(db)
            
        return {
            "success": True,
            "message": "Daily draw successfully triggered",
            "result": result,
            "winners": winners
        }
        
    except Exception as e:
        logger.error(f"Error triggering daily draw: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error triggering daily draw: {str(e)}"
        )

@router.get("/revenue")
async def get_company_revenue(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    claims: dict = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """
    Get company revenue data for a specified date range.
    Only accessible by admin users.
    If no dates are provided, returns data for the last 10 weeks.
    """
    try:
        # Parse dates if provided
        if start_date:
            start = datetime.fromisoformat(start_date).date()
        else:
            # Default to 10 weeks ago
            start = datetime.now().date() - timedelta(weeks=10)
        
        if end_date:
            end = datetime.fromisoformat(end_date).date()
        else:
            # Default to today
            end = datetime.now().date()
        
        # Query revenue records for the specified period
        revenue_records = db.query(CompanyRevenue).filter(
            CompanyRevenue.week_start_date >= start,
            CompanyRevenue.week_start_date <= end
        ).order_by(CompanyRevenue.week_start_date).all()
        
        # Format response
        result = []
        for record in revenue_records:
            result.append({
                "week_start": record.week_start_date.isoformat(),
                "week_end": record.week_end_date.isoformat(),
                "weekly_revenue": record.weekly_revenue,
                "total_revenue": record.total_revenue,
                "streak_rewards_paid": record.streak_rewards_paid,
                "total_streak_rewards_paid": record.total_streak_rewards_paid,
                "created_at": record.created_at.isoformat() if record.created_at else None
            })
        
        # Calculate summary statistics
        total_weeks = len(result)
        total_revenue = sum(record["weekly_revenue"] for record in result)
        total_streak_rewards = sum(record["streak_rewards_paid"] for record in result)
        
        # Include current week's transactions that aren't in the weekly summary yet
        current_monday = datetime.now().date() - timedelta(days=datetime.now().date().weekday())
        current_transactions = db.query(func.sum(Transaction.amount)).filter(
            Transaction.created_at >= current_monday,
            Transaction.amount > 0  # Only count positive transactions as revenue
        ).scalar() or 0
        
        current_streak_rewards = db.query(func.sum(Transaction.amount)).filter(
            Transaction.created_at >= current_monday,
            Transaction.transaction_type == "streak_reward"
        ).scalar() or 0
        
        return {
            "revenue_data": result,
            "summary": {
                "total_weeks": total_weeks,
                "total_revenue": total_revenue,
                "total_streak_rewards": total_streak_rewards,
                "current_week_revenue": current_transactions,
                "current_week_streak_rewards": current_streak_rewards,
                "date_range": {
                    "start": start.isoformat(),
                    "end": end.isoformat()
                }
            }
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid date format: {str(e)}"
        )
    except Exception as e:
        logging.error(f"Error retrieving revenue data: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving revenue data: {str(e)}"
        )

@router.get("/db-integrity/avatars", response_model=Dict[str, Any])
async def check_avatar_integrity(
    fix: bool = Query(False, description="Whether to fix inconsistencies"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to check database integrity for avatar selections
    """
    logger.info(f"Admin checking avatar database integrity, fix={fix}")
    
    try:
        # Get all users with a selected avatar
        users_with_avatars = db.query(User).filter(User.selected_avatar_id != None).all()
        
        # Get list of all available avatar IDs
        all_avatars = {avatar.id: avatar for avatar in db.query(Avatar).all()}
        
        # Track statistics
        total_users = len(users_with_avatars)
        valid_count = 0
        invalid_count = 0
        fixed_count = 0
        invalid_details = []
        
        for user in users_with_avatars:
            # Check if selected avatar exists
            if user.selected_avatar_id not in all_avatars:
                invalid_count += 1
                
                # Record details of invalid selection
                invalid_details.append({
                    "user_id": user.account_id,
                    "username": user.username,
                    "avatar_id": user.selected_avatar_id,
                    "owned_avatars_count": db.query(UserAvatar).filter(UserAvatar.user_id == user.account_id).count()
                })
                
                # Fix if requested
                if fix:
                    # Try to find a valid owned avatar to set instead
                    owned_avatars = db.query(UserAvatar).filter(UserAvatar.user_id == user.account_id).all()
                    if owned_avatars:
                        new_avatar_id = owned_avatars[0].avatar_id
                        if new_avatar_id in all_avatars:
                            user.selected_avatar_id = new_avatar_id
                            logger.info(f"Fixed user {user.account_id} avatar by setting to owned avatar: {new_avatar_id}")
                            fixed_count += 1
                        else:
                            user.selected_avatar_id = None
                            logger.info(f"Reset user {user.account_id} avatar to None as owned avatar {new_avatar_id} is also invalid")
                            fixed_count += 1
                    else:
                        # User has no owned avatars, reset selection
                        user.selected_avatar_id = None
                        logger.info(f"Reset user {user.account_id} avatar to None as they have no owned avatars")
                        fixed_count += 1
            else:
                # Avatar ID is valid
                valid_count += 1
        
        # Commit any changes if fixes were applied
        if fix and fixed_count > 0:
            try:
                db.commit()
                logger.info(f"Successfully committed {fixed_count} avatar fixes")
            except Exception as e:
                db.rollback()
                logger.error(f"Error committing avatar fixes: {str(e)}", exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Error committing avatar fixes: {str(e)}"
                )
        
        return {
            "status": "success",
            "total_users_with_avatars": total_users,
            "valid_selections": valid_count,
            "invalid_selections": invalid_count,
            "fixed_count": fixed_count if fix else 0,
            "repair_mode": fix,
            "invalid_details": invalid_details if len(invalid_details) < 20 else invalid_details[:20]  # Limit output size
        }
    except Exception as e:
        logger.error(f"Error checking avatar integrity: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error checking avatar integrity: {str(e)}"
        )

@router.get("/db-integrity/frames", response_model=Dict[str, Any])
async def check_frame_integrity(
    fix: bool = Query(False, description="Whether to fix inconsistencies"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to check database integrity for frame selections
    """
    logger.info(f"Admin checking frame database integrity, fix={fix}")
    
    try:
        # Get all users with a selected frame
        users_with_frames = db.query(User).filter(User.selected_frame_id != None).all()
        
        # Get list of all available frame IDs
        all_frames = {frame.id: frame for frame in db.query(Frame).all()}
        
        # Track statistics
        total_users = len(users_with_frames)
        valid_count = 0
        invalid_count = 0
        fixed_count = 0
        invalid_details = []
        
        for user in users_with_frames:
            # Check if selected frame exists
            if user.selected_frame_id not in all_frames:
                invalid_count += 1
                
                # Record details of invalid selection
                invalid_details.append({
                    "user_id": user.account_id,
                    "username": user.username,
                    "frame_id": user.selected_frame_id,
                    "owned_frames_count": db.query(UserFrame).filter(UserFrame.user_id == user.account_id).count()
                })
                
                # Fix if requested
                if fix:
                    # Try to find a valid owned frame to set instead
                    owned_frames = db.query(UserFrame).filter(UserFrame.user_id == user.account_id).all()
                    if owned_frames:
                        new_frame_id = owned_frames[0].frame_id
                        if new_frame_id in all_frames:
                            user.selected_frame_id = new_frame_id
                            logger.info(f"Fixed user {user.account_id} frame by setting to owned frame: {new_frame_id}")
                            fixed_count += 1
                        else:
                            user.selected_frame_id = None
                            logger.info(f"Reset user {user.account_id} frame to None as owned frame {new_frame_id} is also invalid")
                            fixed_count += 1
                    else:
                        # User has no owned frames, reset selection
                        user.selected_frame_id = None
                        logger.info(f"Reset user {user.account_id} frame to None as they have no owned frames")
                        fixed_count += 1
            else:
                # Frame ID is valid
                valid_count += 1
        
        # Commit any changes if fixes were applied
        if fix and fixed_count > 0:
            try:
                db.commit()
                logger.info(f"Successfully committed {fixed_count} frame fixes")
            except Exception as e:
                db.rollback()
                logger.error(f"Error committing frame fixes: {str(e)}", exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Error committing frame fixes: {str(e)}"
                )
        
        return {
            "status": "success",
            "total_users_with_frames": total_users,
            "valid_selections": valid_count,
            "invalid_selections": invalid_count,
            "fixed_count": fixed_count if fix else 0,
            "repair_mode": fix,
            "invalid_details": invalid_details if len(invalid_details) < 20 else invalid_details[:20]  # Limit output size
        }
    except Exception as e:
        logger.error(f"Error checking frame integrity: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error checking frame integrity: {str(e)}"
        ) 