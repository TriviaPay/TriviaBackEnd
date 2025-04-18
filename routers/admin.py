import os
from datetime import date, datetime, time, timedelta
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Body, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import pytz
import logging
import json
from sqlalchemy import func # Import func for count

from db import get_db
from models import TriviaDrawConfig, TriviaDrawWinner, CompanyRevenue, Transaction
from routers.dependencies import get_admin_user
from rewards_logic import perform_draw

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
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to get the current draw configuration from the database.
    """
    try:
        logger.info("--- Entering get_draw_config ---")
        
        # Check for multiple config rows (potential issue indicator)
        config_count = db.query(func.count(TriviaDrawConfig.id)).scalar()
        if config_count > 1:
            logger.warning(f"Multiple ({config_count}) rows found in trivia_draw_config table. Only the latest (by ID) will be used.")
            
        config: Optional[TriviaDrawConfig] = db.query(TriviaDrawConfig).order_by(TriviaDrawConfig.id.desc()).first() # Get the latest one

        custom_data: Dict[str, Any] = {}
        if config and config.custom_data:
            try:
                custom_data = json.loads(config.custom_data)
                logger.info(f"Parsed custom_data from DB: {custom_data}")
            except json.JSONDecodeError:
                logger.error(f"Failed to parse custom_data from DB ID={config.id}: {config.custom_data}", exc_info=True)
                # Proceed with defaults, but log error

        if not config:
            logger.info("No existing config found. Returning defaults.")
            # Simulate a default config for response, but don't save it here. Let PUT create it.
            is_custom_resp = False
            custom_winner_count_resp = None
            draw_hour_resp = DEFAULT_DRAW_HOUR
            draw_minute_resp = DEFAULT_DRAW_MINUTE
            draw_timezone_resp = DEFAULT_TIMEZONE
            custom_data_resp = {"draw_time_hour": draw_hour_resp, "draw_time_minute": draw_minute_resp, "draw_timezone": draw_timezone_resp}
        else:
            logger.info(f"Found existing config in DB: ID={config.id}, is_custom={config.is_custom}, count={config.custom_winner_count}")
            is_custom_resp = config.is_custom
            custom_winner_count_resp = config.custom_winner_count
            # Get time/timezone from custom_data, falling back to defaults
            draw_hour_resp = custom_data.get("draw_time_hour", DEFAULT_DRAW_HOUR)
            draw_minute_resp = custom_data.get("draw_time_minute", DEFAULT_DRAW_MINUTE)
            draw_timezone_resp = custom_data.get("draw_timezone", DEFAULT_TIMEZONE)
            custom_data_resp = custom_data # Return the full parsed custom_data

        # Log the final state being used for the response
        logger.info(f"Final config values for GET response: is_custom={is_custom_resp}, count={custom_winner_count_resp}, hour={draw_hour_resp}, min={draw_minute_resp}, tz={draw_timezone_resp}")

        return DrawConfigResponse(
            is_custom=is_custom_resp,
            custom_winner_count=custom_winner_count_resp,
            draw_time_hour=draw_hour_resp,
            draw_time_minute=draw_minute_resp,
            draw_timezone=draw_timezone_resp,
            custom_data=custom_data_resp # Pass the potentially extended custom_data back
        )

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

@router.post("/trigger-draw", response_model=DrawResponse)
async def trigger_draw(
    draw_date: date = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to manually trigger a draw for a specific date.
    """
    try:
        # If no date provided, use today's date
        if draw_date is None:
            draw_date = date.today()
            
        # Check if a draw has already been performed for this date
        existing_draw = db.query(TriviaDrawWinner).filter(
            TriviaDrawWinner.draw_date == draw_date
        ).first()
        
        if existing_draw:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Draw for {draw_date} has already been performed"
            )
        
        # Perform the draw
        result = perform_draw(db, draw_date)
        
        if result["status"] == "no_participants":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No eligible participants for draw on {draw_date}"
            )
        
        return DrawResponse(
            status=result["status"],
            draw_date=result["draw_date"],
            total_participants=result["total_participants"],
            total_winners=result["total_winners"],
            prize_pool=result["prize_pool"],
            winners=result["winners"]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error triggering draw: {str(e)}"
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