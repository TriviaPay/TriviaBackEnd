import os
from datetime import date, datetime, time
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Body, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import pytz
import logging

from db import get_db
from models import TriviaDrawConfig, TriviaDrawWinner
from routers.dependencies import get_admin_user
from rewards_logic import perform_draw

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

class DrawResponse(BaseModel):
    status: str
    draw_date: date
    total_participants: int
    total_winners: int
    prize_pool: float
    winners: List[Dict[str, Any]]

@router.get("/draw-config", response_model=DrawConfigResponse)
async def get_draw_config(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to get the current draw configuration.
    """
    try:
        logging.info("Getting draw config from admin.py endpoint")
        config = db.query(TriviaDrawConfig).first()
        if not config:
            config = TriviaDrawConfig(
                is_custom=False,
                custom_winner_count=None
            )
            db.add(config)
            db.commit()
            db.refresh(config)
            logging.info("Created new default config")
        
        logging.info(f"Current config: is_custom={config.is_custom}, custom_winner_count={config.custom_winner_count}")
        
        # Get draw time from environment variables
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

@router.put("/draw-config", response_model=DrawConfigResponse)
async def update_draw_config(
    config: DrawConfigUpdateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to update the draw configuration.
    """
    try:
        logging.info(f"Updating draw config: {config}")
        # Validate timezone if provided
        if config.draw_timezone:
            try:
                pytz.timezone(config.draw_timezone)
            except pytz.exceptions.UnknownTimeZoneError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid timezone: {config.draw_timezone}"
                )
        
        # Get or create config in database
        db_config = db.query(TriviaDrawConfig).first()
        if not db_config:
            db_config = TriviaDrawConfig(
                is_custom=False,
                custom_winner_count=None
            )
            db.add(db_config)
            logging.info("Created new config")
        
        # Update database config
        if config.is_custom is not None:
            db_config.is_custom = config.is_custom
        
        if config.custom_winner_count is not None:
            db_config.custom_winner_count = config.custom_winner_count
        
        # Update environment variables for draw time
        if config.draw_time_hour is not None:
            os.environ["DRAW_TIME_HOUR"] = str(config.draw_time_hour)
        
        if config.draw_time_minute is not None:
            os.environ["DRAW_TIME_MINUTE"] = str(config.draw_time_minute)
        
        if config.draw_timezone:
            os.environ["DRAW_TIMEZONE"] = config.draw_timezone
        
        db.commit()
        db.refresh(db_config)
        logging.info(f"Updated config: is_custom={db_config.is_custom}, custom_winner_count={db_config.custom_winner_count}")
        
        # Get current values for response
        draw_time_hour = int(os.environ.get("DRAW_TIME_HOUR", "20"))
        draw_time_minute = int(os.environ.get("DRAW_TIME_MINUTE", "0"))
        draw_timezone = os.environ.get("DRAW_TIMEZONE", "US/Eastern")
        
        return DrawConfigResponse(
            is_custom=db_config.is_custom,
            custom_winner_count=db_config.custom_winner_count,
            draw_time_hour=draw_time_hour,
            draw_time_minute=draw_time_minute,
            draw_timezone=draw_timezone
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Error updating draw configuration: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating draw configuration: {str(e)}"
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