import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session
from db import SessionLocal
from rewards_logic import perform_draw, reset_daily_eligibility_flags
from cleanup_unused_questions import cleanup_unused_questions

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler = AsyncIOScheduler()

def get_draw_time():
    """Get draw time configuration from environment variables"""
    import os
    return {
        "hour": int(os.environ.get("DRAW_TIME_HOUR", "20")),  # Default 8 PM
        "minute": int(os.environ.get("DRAW_TIME_MINUTE", "0")),  # Default 0 minutes
        "timezone": os.environ.get("DRAW_TIMEZONE", "US/Eastern")  # Default EST
    }

def schedule_draws():
    """
    Schedule the daily draw and question reset.
    
    Timing:
    - 8:00 PM EST: Process yesterday's draw (winners selected)
    - 8:01 PM EST: Reset questions and eligibility flags for new day
    - Questions available from 8:01 PM to next 8:00 PM
    """
    global scheduler
    
    # Get draw time from environment variables
    draw_time = get_draw_time()
    hour = draw_time["hour"]
    minute = draw_time["minute"]
    timezone = draw_time["timezone"]
    
    logger.info(f"Scheduling daily draw at {hour}:{minute} {timezone}")
    logger.info(f"Question reset at {hour}:{minute+1} {timezone}")
    
    # Schedule the daily draw job (process yesterday's draw)
    scheduler.add_job(
        run_daily_draw,
        CronTrigger(hour=hour, minute=minute, timezone=timezone),
        id="daily_draw",
        replace_existing=True,
        misfire_grace_time=3600  # Allow the job to run up to 1 hour late
    )
    
    # Schedule question reset job (1 minute after draw)
    scheduler.add_job(
        reset_daily_questions,
        CronTrigger(hour=hour, minute=minute+1, timezone=timezone),
        id="question_reset",
        replace_existing=True,
        misfire_grace_time=3600
    )
    
    # Schedule monthly subscription reset job (11:59 PM EST on last day of each month)
    scheduler.add_job(
        run_monthly_subscription_reset,
        CronTrigger(day="last", hour=23, minute=59, timezone=timezone),
        id="monthly_subscription_reset",
        replace_existing=True,
        misfire_grace_time=3600
    )

async def run_daily_draw():
    """
    Process yesterday's draw at 8:00 PM EST.
    This processes the draw for users who answered questions correctly
    from 8:01 PM yesterday to 8:00 PM today.
    """
    try:
        logger.info(f"Starting daily draw at {datetime.now()}")
        db: Session = SessionLocal()
        
        try:
            # Process yesterday's draw
            yesterday = date.today() - timedelta(days=1)
            result = perform_draw(db, yesterday)
            
            if result["status"] == "success":
                logger.info(f"Draw completed successfully: {result['total_winners']} winners from {result['total_participants']} participants")
            else:
                logger.info(f"Draw result: {result['status']} - {result.get('message', '')}")
                
        except Exception as db_error:
            logger.error(f"Database error during draw: {str(db_error)}")
        finally:
            db.close()
        
    except Exception as e:
        logger.error(f"Error running daily draw: {str(e)}")

async def reset_daily_questions():
    """
    Reset daily questions at 8:01 PM EST.
    This makes new questions available for the next 24-hour period.
    """
    try:
        logger.info(f"Starting daily question reset at {datetime.now()}")
        
        # Clean up unused questions from today
        cleanup_unused_questions()
        
        # Reset eligibility flags for new day
        db: Session = SessionLocal()
        try:
            reset_daily_eligibility_flags(db)
            logger.info("Successfully reset eligibility flags for new day")
        except Exception as e:
            logger.error(f"Error resetting eligibility flags: {e}")
        finally:
            db.close()
        
    except Exception as e:
        logger.error(f"Error resetting daily questions: {str(e)}")

async def run_monthly_subscription_reset():
    """
    Reset monthly subscription flags at 11:59 PM EST on the last day of each month.
    """
    try:
        logger.info(f"Starting monthly subscription reset at {datetime.now()}")
        db: Session = SessionLocal()
        
        try:
            # Reset all subscription flags
            db.query(User).update({"subscription_flag": False})
            db.commit()
            logger.info("Successfully reset all subscription flags")
            
        except Exception as db_error:
            db.rollback()
            logger.error(f"Database error during subscription reset: {str(db_error)}")
        finally:
            db.close()
        
    except Exception as e:
        logger.error(f"Error running monthly subscription reset: {str(e)}")

def start_scheduler():
    """
    Start the background scheduler.
    This should be called when the application starts.
    """
    global scheduler
    
    if not scheduler.running:
        schedule_draws()
        scheduler.start()
        logger.info("Scheduler started successfully")
    else:
        logger.warning("Scheduler is already running")

def stop_scheduler():
    """
    Stop the background scheduler.
    This should be called when the application shuts down.
    """
    global scheduler
    
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped successfully")
    else:
        logger.warning("Scheduler is not running")

# For testing
if __name__ == "__main__":
    start_scheduler()
    import asyncio
    asyncio.run(run_daily_draw())
