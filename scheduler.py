from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
import pytz
from db import SessionLocal
import logging
import requests
import os
from sqlalchemy import func
from models import DailyQuestion, Trivia
from sqlalchemy.orm import Session
import json

from db import get_db
from rewards_logic import perform_draw, get_draw_time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler = None

# Get the draw time from environment or use default (8 PM EST)
DRAW_TIME_HOUR = int(os.getenv("DRAW_TIME_HOUR", "20"))  # 8 PM in 24-hour format
DRAW_TIME_MINUTE = int(os.getenv("DRAW_TIME_MINUTE", "0"))
DRAW_TIMEZONE = os.getenv("DRAW_TIMEZONE", "US/Eastern")

def schedule_draws():
    """
    Schedule the daily draw to run at the configured time.
    """
    global scheduler
    
    # Get draw time from environment variables
    draw_time = get_draw_time()
    hour = draw_time["hour"]
    minute = draw_time["minute"]
    timezone = draw_time["timezone"]
    
    logger.info(f"Scheduling daily draw at {hour}:{minute} {timezone}")
    
    # Schedule the daily draw job
    scheduler.add_job(
        run_daily_draw,
        CronTrigger(hour=hour, minute=minute, timezone=timezone),
        id="daily_draw",
        replace_existing=True,
        misfire_grace_time=3600  # Allow the job to run up to 1 hour late
    )

async def run_daily_draw():
    """
    Run the daily draw for today's date.
    This is called automatically by the scheduler at the configured time.
    """
    logger.info("Running daily draw...")
    
    # Create a database session
    db = next(get_db())
    try:
        # Get yesterday's date (for draws that run after midnight)
        yesterday = date.today() - timedelta(days=1)
        
        # Perform the draw
        result = perform_draw(db, yesterday)
        
        logger.info(f"Daily draw completed: {result}")
        
    except Exception as e:
        logger.error(f"Error running daily draw: {str(e)}")
    finally:
        db.close()

async def reset_daily_questions():
    """
    Reset daily questions at 8:01 PM EST. 
    Mark unused questions as undone so they can be used again in the future.
    """
    try:
        logging.info(f"Starting daily question reset at {datetime.now()}")
        db: Session = SessionLocal()
        
        try:
            today = datetime.utcnow().date()
            
            # Get all daily questions from today
            today_questions = db.query(DailyQuestion).filter(
                func.date(DailyQuestion.date) == today
            ).all()
            
            # Count total, used, and unused questions
            total_questions = len(today_questions)
            used_questions = sum(1 for q in today_questions if q.is_used)
            unused_questions = total_questions - used_questions
            
            logging.info(f"Found {total_questions} questions for today, {used_questions} used and {unused_questions} unused")
            
            # For each unused question, mark the Trivia question as undone
            # so it can be reused in the future
            for daily_q in today_questions:
                if not daily_q.is_used:
                    # Get the Trivia question
                    trivia_q = db.query(Trivia).filter(
                        Trivia.question_number == daily_q.question_number
                    ).first()
                    
                    if trivia_q:
                        # Mark it as undone so it can be used again
                        trivia_q.question_done = False
                        trivia_q.que_displayed_date = None
                        logging.info(f"Marked question {trivia_q.question_number} as undone")
            
            db.commit()
            logging.info(f"Successfully reset {unused_questions} unused questions")
            
        except Exception as db_error:
            db.rollback()
            logging.error(f"Database error during question reset: {str(db_error)}")
        finally:
            db.close()
        
    except Exception as e:
        logging.error(f"Error resetting daily questions: {str(e)}")

def start_scheduler():
    """
    Start the background scheduler.
    This should be called when the application starts.
    """
    global scheduler
    
    if scheduler is None:
        logger.info("Starting the scheduler...")
        scheduler = AsyncIOScheduler()
        
        # Schedule the draws
        schedule_draws()
        
        # Start the scheduler
        scheduler.start()
        logger.info("Scheduler started")
    else:
        logger.info("Scheduler already running")

def stop_scheduler():
    """
    Stop the background scheduler.
    This should be called when the application shuts down.
    """
    global scheduler
    
    if scheduler and scheduler.running:
        logger.info("Stopping scheduler...")
        scheduler.shutdown()
        scheduler = None
        logger.info("Scheduler stopped") 