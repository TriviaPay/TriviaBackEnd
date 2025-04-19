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
from models import TriviaDrawConfig, TriviaDrawWinner, CompanyRevenue, Transaction, User, Avatar, UserAvatar, Frame, UserFrame
from routers.dependencies import get_admin_user
from rewards_logic import perform_draw, get_daily_winners, get_weekly_winners

# Configure logging at the top level if not already done
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])

# Request models
class DrawConfigUpdateRequest(BaseModel):
    is_custom: Optional[bool] = Field(None, description="Whether to use custom winner count")
    custom_winner_count: Optional[int] = Field(None, description="Custom number of winners when is_custom is True")
    draw_time_hour: Optional[int] = Field(None, ge=0, le=23, description="Hour of the day for the draw (0-23)")
    draw_time_minute: Optional[int] = Field(None, ge=0, le=59, description="Minute of the hour for the draw (0-59)")
    draw_timezone: Optional[str] = Field(None, description="Timezone for the draw (e.g., US/Eastern)")

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

@router.get("/draw-config", response_model=DrawConfigResponse)
async def get_draw_config(
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
                daily_pool_amount=1000.0,
                daily_winners_count=5,
                weekly_pool_amount=5000.0,
                weekly_winners_count=10,
                weekly_draw_day=5,  # Friday
                automatic_draws=True
            )
            db.add(draw_config)
            db.commit()
            db.refresh(draw_config)
            
            logger.info("Created default draw configuration")
            
        # Fetch the last daily and weekly draw timestamps
        last_daily_draw = db.query(TriviaDrawWinner).filter(
            TriviaDrawWinner.draw_type == 'daily'
        ).order_by(TriviaDrawWinner.created_at.desc()).first()
        
        last_weekly_draw = db.query(TriviaDrawWinner).filter(
            TriviaDrawWinner.draw_type == 'weekly'
        ).order_by(TriviaDrawWinner.created_at.desc()).first()
        
        # Return response
        return {
            "config": {
                "daily_pool_amount": draw_config.daily_pool_amount,
                "daily_winners_count": draw_config.daily_winners_count,
                "weekly_pool_amount": draw_config.weekly_pool_amount,
                "weekly_winners_count": draw_config.weekly_winners_count,
                "weekly_draw_day": draw_config.weekly_draw_day,
                "automatic_draws": draw_config.automatic_draws
            },
            "last_draw_info": {
                "last_daily_draw": last_daily_draw.created_at if last_daily_draw else None,
                "last_weekly_draw": last_weekly_draw.created_at if last_weekly_draw else None
            }
        }

    except Exception as e:
        logger.error(f"Error getting draw configuration: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting draw configuration: {str(e)}"
        )

@router.put("/draw-config", response_model=DrawConfigResponse)
async def update_draw_config(
    req_config: DrawConfigUpdateRequest, # Renamed request model instance
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to update the draw configuration in the database.
    """
    try:
        logger.info(f"--- Entering update_draw_config with payload: {req_config.dict(exclude_unset=True)} ---") # Log only provided fields
        
        # Check for multiple config rows (potential issue indicator)
        config_count = db.query(func.count(TriviaDrawConfig.id)).scalar()
        if config_count > 1:
            logger.warning(f"Multiple ({config_count}) rows found in trivia_draw_config table. Only the latest (by ID) will be updated.")

        # Validate timezone if provided in request
        if req_config.draw_timezone:
            try:
                pytz.timezone(req_config.draw_timezone)
                logger.info(f"Timezone '{req_config.draw_timezone}' is valid.")
            except pytz.exceptions.UnknownTimeZoneError:
                logger.error(f"Invalid timezone provided: {req_config.draw_timezone}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid timezone: {req_config.draw_timezone}"
                )

        # Get the latest config record to update, or create if none exists
        db_config: Optional[TriviaDrawConfig] = db.query(TriviaDrawConfig).order_by(TriviaDrawConfig.id.desc()).first() # Get the latest one

        current_custom_data: Dict[str, Any] = {}
        if db_config:
             logger.info(f"Found existing config to update: ID={db_config.id}, is_custom={db_config.is_custom}, count={db_config.custom_winner_count}, custom_data='{db_config.custom_data}'")
             # Load existing custom_data if available
             if db_config.custom_data:
                 try:
                     current_custom_data = json.loads(db_config.custom_data)
                 except json.JSONDecodeError:
                     logger.error(f"Failed to parse existing custom_data ID={db_config.id}: {db_config.custom_data}. Starting fresh.", exc_info=True)
                     current_custom_data = {}
        else:
            logger.info("No existing config found. Creating new one.")
            db_config = TriviaDrawConfig(
                is_custom=False, # Start with default
                custom_winner_count=None,
                custom_data=None # Will be populated below
            )
            db.add(db_config)
            current_custom_data = {} # Start with empty custom data for new record

        # --- Prepare updates ---
        updated_fields = []
        
        # Update is_custom
        if req_config.is_custom is not None and db_config.is_custom != req_config.is_custom:
            db_config.is_custom = req_config.is_custom
            updated_fields.append(f"is_custom={db_config.is_custom}")

        # Update custom_winner_count
        if req_config.custom_winner_count is not None and db_config.custom_winner_count != req_config.custom_winner_count:
            db_config.custom_winner_count = req_config.custom_winner_count
            updated_fields.append(f"custom_winner_count={db_config.custom_winner_count}")

        # --- Update custom_data field ---
        new_custom_data = current_custom_data.copy() # Start with existing or empty

        # Update draw time/timezone in custom_data if provided in request
        if req_config.draw_time_hour is not None:
            new_custom_data["draw_time_hour"] = req_config.draw_time_hour
            updated_fields.append(f"custom_data.draw_time_hour={req_config.draw_time_hour}")
        if req_config.draw_time_minute is not None:
            new_custom_data["draw_time_minute"] = req_config.draw_time_minute
            updated_fields.append(f"custom_data.draw_time_minute={req_config.draw_time_minute}")
        if req_config.draw_timezone is not None:
            new_custom_data["draw_timezone"] = req_config.draw_timezone
            updated_fields.append(f"custom_data.draw_timezone='{req_config.draw_timezone}'")

        # Ensure defaults are present in custom_data if not set by request or existing data
        new_custom_data.setdefault("draw_time_hour", DEFAULT_DRAW_HOUR)
        new_custom_data.setdefault("draw_time_minute", DEFAULT_DRAW_MINUTE)
        new_custom_data.setdefault("draw_timezone", DEFAULT_TIMEZONE)

        # Save the updated custom_data back to the model field as JSON string
        updated_custom_data_json = json.dumps(new_custom_data)
        if db_config.custom_data != updated_custom_data_json:
             db_config.custom_data = updated_custom_data_json
             # Log the change separately if custom_data content changed
             if not any(f.startswith("custom_data.") for f in updated_fields):
                 updated_fields.append(f"custom_data='{updated_custom_data_json}'")
             else: # replace individual field logs with the full json
                 updated_fields = [f for f in updated_fields if not f.startswith("custom_data.")]
                 updated_fields.append(f"custom_data='{updated_custom_data_json}'")

        # --- Commit Changes ---
        if not updated_fields:
             logger.info("No database fields needed updating based on the request payload.")
        else:
            logger.info(f"Attempting to commit updates: {', '.join(updated_fields)}")
            try:
                db.commit()
                logger.info(f"Successfully committed updates for config ID={db_config.id}")
            except Exception as commit_exc:
                logger.error(f"Error committing config updates ID={db_config.id}: {commit_exc}", exc_info=True)
                db.rollback()
                raise HTTPException(status_code=500, detail="Failed to save config updates")

        # Refresh to get the latest state including DB defaults/triggers if any
        try:
            db.refresh(db_config)
            logger.info(f"Refreshed DB state after commit: ID={db_config.id}, is_custom={db_config.is_custom}, count={db_config.custom_winner_count}, custom_data='{db_config.custom_data}'")
        except Exception as refresh_exc:
             logger.error(f"Error refreshing config object after commit ID={db_config.id}: {refresh_exc}", exc_info=True)
             # If refresh fails, we might have stale data, but proceed cautiously.

        # --- Construct Response from the refreshed DB State ---
        final_custom_data: Dict[str, Any] = {}
        refreshed_is_custom = db_config.is_custom
        refreshed_winner_count = db_config.custom_winner_count
        
        if db_config.custom_data:
             try:
                 final_custom_data = json.loads(db_config.custom_data)
             except json.JSONDecodeError:
                 logger.error(f"Failed to parse refreshed custom_data for response ID={db_config.id}: {db_config.custom_data}", exc_info=True)
                 # Use defaults if parsing fails
                 final_custom_data = {
                     "draw_time_hour": DEFAULT_DRAW_HOUR,
                     "draw_time_minute": DEFAULT_DRAW_MINUTE,
                     "draw_timezone": DEFAULT_TIMEZONE
                 }
        else: # Handle case where custom_data might be None after refresh
             logger.warning(f"Refreshed custom_data is None for ID={db_config.id}. Using defaults for response time/tz.")
             final_custom_data = {
                 "draw_time_hour": DEFAULT_DRAW_HOUR,
                 "draw_time_minute": DEFAULT_DRAW_MINUTE,
                 "draw_timezone": DEFAULT_TIMEZONE
             }


        # Use values directly from refreshed db_config and parsed final_custom_data for response
        resp_hour = final_custom_data.get("draw_time_hour", DEFAULT_DRAW_HOUR)
        resp_minute = final_custom_data.get("draw_time_minute", DEFAULT_DRAW_MINUTE)
        resp_timezone = final_custom_data.get("draw_timezone", DEFAULT_TIMEZONE)

        logger.info(f"Final PUT response values: is_custom={refreshed_is_custom}, count={refreshed_winner_count}, hour={resp_hour}, min={resp_minute}, tz={resp_timezone}")

        return DrawConfigResponse(
            is_custom=refreshed_is_custom,
            custom_winner_count=refreshed_winner_count,
            draw_time_hour=resp_hour,
            draw_time_minute=resp_minute,
            draw_timezone=resp_timezone,
            custom_data=final_custom_data # Return full custom data dictionary
        )

    except HTTPException as http_exc:
        # Log HTTPExceptions before raising them
        logger.error(f"HTTPException in update_draw_config: Status={http_exc.status_code}, Detail={http_exc.detail}")
        raise http_exc # Re-raise the original HTTPException
    except Exception as e:
        # Rollback in case of unexpected errors before commit happened
        try:
            db.rollback()
            logger.info("Rolled back transaction due to unexpected error.")
        except Exception as rb_exc:
             logger.error(f"Error during rollback: {rb_exc}", exc_info=True)
             
        logger.error(f"Unexpected error updating draw configuration: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error updating draw configuration: {str(e)}"
        )

@router.post("/trigger-draw", response_model=dict)
async def trigger_draw(
    request: Request,
    draw_type: str = Query(..., description="Type of draw to trigger ('daily' or 'weekly')"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Trigger a draw manually
    
    This endpoint requires admin privileges.
    """
    logger.info(f"Admin triggering {draw_type} draw manually")
    
    # Validate draw type
    if draw_type not in ['daily', 'weekly']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Draw type must be 'daily' or 'weekly'"
        )

    try:
        # Call the perform_draw function with today's date
        result = perform_draw(db)
        
        # Get winners based on draw type
        if draw_type == 'daily':
            winners = get_daily_winners(db)
        else:
            winners = get_weekly_winners(db)
            
        return {
            "success": True,
            "message": f"{draw_type.capitalize()} draw successfully triggered",
            "result": result,
            "winners": winners
        }
        
    except Exception as e:
        logger.error(f"Error triggering {draw_type} draw: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error triggering {draw_type} draw: {str(e)}"
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