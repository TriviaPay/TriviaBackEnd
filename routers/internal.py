from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from datetime import date, timedelta, datetime
import os
from db import get_db
from rewards_logic import perform_draw, reset_monthly_subscriptions, reset_weekly_daily_rewards
import logging
from updated_scheduler import get_detailed_draw_metrics, get_detailed_reset_metrics, get_detailed_monthly_reset_metrics

router = APIRouter(prefix="/internal", tags=["Internal"])

@router.post("/daily-draw")
async def internal_daily_draw(
    secret: str = Header(..., alias="X-Secret", description="Secret key for internal calls"),
    db: Session = Depends(get_db)
):
    """
    Internal endpoint for daily draw triggered by external cron (cron-job.org).
    
    This endpoint:
    1. Gets detailed metrics for yesterday's draw date
    2. Performs the draw using get_eligible_participants() which queries TriviaUserDaily directly
    3. Returns comprehensive results including diagnostics
    
    The eligibility check uses TriviaUserDaily to ensure date-specific accuracy.
    """
    if secret != os.getenv("INTERNAL_SECRET"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        # Run daily draw for yesterday
        yesterday = date.today() - timedelta(days=1)
        
        logging.info(f"🎯 Starting daily draw for {yesterday} via external cron")
        
        # Get detailed metrics before performing draw
        # This uses the updated get_detailed_draw_metrics() which queries TriviaUserDaily
        logging.info("📊 Collecting detailed draw metrics...")
        try:
            metrics = get_detailed_draw_metrics(db, yesterday)
            logging.info(f"✅ Metrics collected: {metrics.get('eligible_and_subscribed', 0)} eligible participants")
            
            # Check if there's an error in metrics collection
            if "error" in metrics:
                logging.error(f"❌ Error in metrics collection: {metrics['error']}")
                # Continue anyway - metrics error shouldn't block the draw
        except Exception as metrics_error:
            logging.error(f"❌ Failed to collect metrics: {str(metrics_error)}", exc_info=True)
            metrics = {"error": str(metrics_error)}
        
        # Perform the draw
        # This uses get_eligible_participants() which queries TriviaUserDaily directly
        logging.info("🎲 Performing draw...")
        try:
            result = perform_draw(db, yesterday)
            logging.info(f"✅ Draw completed: {result.get('status', 'unknown')} - {result.get('total_participants', 0)} participants, {result.get('total_winners', 0)} winners")
        except Exception as draw_error:
            logging.error(f"❌ Failed to perform draw: {str(draw_error)}", exc_info=True)
            # Re-raise so the error is returned to cron-job.org
            raise
        
        return {
            "status": "success",
            "triggered_by": "external_cron",
            "draw_date": yesterday.isoformat(),
            "detailed_metrics": metrics,
            "draw_result": result,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logging.error(f"💥 Fatal error in daily draw: {str(e)}", exc_info=True)
        # Return error details in response so cron-job.org logs show the issue
        raise HTTPException(
            status_code=500,
            detail=f"Error in daily draw: {str(e)}"
        )

@router.post("/question-reset")
async def internal_question_reset(
    secret: str = Header(..., alias="X-Secret", description="Secret key for internal calls"),
    db: Session = Depends(get_db)
):
    """Internal endpoint for question reset triggered by external cron"""
    if secret != os.getenv("INTERNAL_SECRET"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        # Get detailed metrics before reset
        logging.info("📊 Collecting detailed reset metrics...")
        metrics = get_detailed_reset_metrics(db)
        
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
            "triggered_by": "external_cron",
            "detailed_metrics": metrics,
            "timestamp": datetime.now().isoformat()
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
        # Get detailed metrics before reset
        logging.info("📊 Collecting detailed monthly reset metrics...")
        metrics = get_detailed_monthly_reset_metrics(db)
        
        # Reset monthly subscriptions
        reset_monthly_subscriptions(db)
        
        logging.info("Monthly subscription reset completed via external cron")
        return {
            "status": "success",
            "message": "All subscription flags reset",
            "triggered_by": "external_cron",
            "detailed_metrics": metrics,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logging.error(f"Error in monthly reset: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/weekly-rewards-reset")
async def internal_weekly_rewards_reset(
    secret: str = Header(..., alias="X-Secret", description="Secret key for internal calls"),
    db: Session = Depends(get_db)
):
    """Internal endpoint for weekly daily rewards reset triggered by external cron"""
    if secret != os.getenv("INTERNAL_SECRET"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        # Reset weekly daily rewards
        logging.info("🔄 Resetting weekly daily rewards...")
        reset_weekly_daily_rewards(db)
        
        logging.info("Weekly daily rewards reset completed via external cron")
        return {
            "status": "success",
            "message": "All weekly daily rewards reset",
            "triggered_by": "external_cron",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logging.error(f"Error in weekly rewards reset: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health")
async def internal_health():
    """Health check for external cron services"""
    return {
        "status": "healthy",
        "service": "triviapay-internal",
        "timestamp": datetime.utcnow().isoformat()
    }
