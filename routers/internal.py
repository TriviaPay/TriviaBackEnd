from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from datetime import date, timedelta, datetime
import os
from db import get_db
from rewards_logic import perform_draw, reset_monthly_subscriptions
import logging

router = APIRouter(prefix="/internal", tags=["Internal"])

@router.post("/daily-draw")
async def internal_daily_draw(
    secret: str = Header(..., alias="X-Secret", description="Secret key for internal calls"),
    db: Session = Depends(get_db)
):
    """Internal endpoint for daily draw triggered by external cron"""
    if secret != os.getenv("INTERNAL_SECRET"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        # Run daily draw for yesterday
        yesterday = date.today() - timedelta(days=1)
        result = perform_draw(db, yesterday)
        
        logging.info(f"Daily draw completed via external cron: {result}")
        return {
            "status": "success",
            "result": result,
            "triggered_by": "external_cron"
        }
    except Exception as e:
        logging.error(f"Error in daily draw: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/question-reset")
async def internal_question_reset(
    secret: str = Header(..., alias="X-Secret", description="Secret key for internal calls"),
    db: Session = Depends(get_db)
):
    """Internal endpoint for question reset triggered by external cron"""
    if secret != os.getenv("INTERNAL_SECRET"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        # Import here to avoid circular imports
        from cleanup_unused_questions import cleanup_unused_questions
        from rewards_logic import reset_daily_eligibility_flags
        
        # Clean up unused questions
        cleanup_unused_questions()
        
        # Reset eligibility flags
        reset_daily_eligibility_flags(db)
        
        logging.info("Question reset completed via external cron")
        return {
            "status": "success",
            "message": "Questions reset and eligibility flags cleared",
            "triggered_by": "external_cron"
        }
    except Exception as e:
        logging.error(f"Error in question reset: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/monthly-reset")
async def internal_monthly_reset(
    secret: str = Header(..., alias="X-Secret", description="Secret key for internal calls"),
    db: Session = Depends(get_db)
):
    """Internal endpoint for monthly subscription reset triggered by external cron"""
    if secret != os.getenv("INTERNAL_SECRET"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        # Reset monthly subscriptions
        reset_monthly_subscriptions(db)
        
        logging.info("Monthly subscription reset completed via external cron")
        return {
            "status": "success",
            "message": "All subscription flags reset",
            "triggered_by": "external_cron"
        }
    except Exception as e:
        logging.error(f"Error in monthly reset: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health")
async def internal_health():
    """Health check for external cron services"""
    return {
        "status": "healthy",
        "service": "triviapay-internal",
        "timestamp": datetime.utcnow().isoformat()
    }
