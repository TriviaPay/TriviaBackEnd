from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
from sqlalchemy import func
from contextlib import contextmanager
from datetime import datetime, timedelta
import logging
import pytz

from models import DailyQuestion, Trivia, User, TriviaDrawConfig, TriviaDrawWinner
from db import get_db
from rewards_logic import perform_draw, is_draw_time

# Configure logging
logger = logging.getLogger(__name__)

# Create a global scheduler instance that can be accessed by other modules
scheduler = BackgroundScheduler()

@contextmanager
def get_session():
    """Provide a transactional scope around a series of operations."""
    db = next(get_db())
    try:
        yield db
    finally:
        db.close()

# ========= Scheduled Tasks =========

def reset_daily_questions():
    """Reset daily questions at midnight"""
    logger.info("Running scheduled task: reset_daily_questions")
    try:
        with get_session() as db:
            # Get today's date
            today = datetime.utcnow().date()
            
            # Check if questions already allocated for today
            existing_questions = db.query(DailyQuestion).filter(
                DailyQuestion.date == today
            ).first()
            
            if existing_questions:
                logger.info(f"Questions already allocated for {today}, skipping")
                return
            
            # Get unused questions
            unused_questions = db.query(Trivia).filter(
                Trivia.question_done == False
            ).order_by(func.random()).limit(4).all()
        
            if len(unused_questions) < 4:
                # Not enough unused questions, reset some previous ones
                logger.warning("Not enough unused questions, resetting some previous ones")
                all_questions = db.query(Trivia).order_by(func.random()).limit(4).all()
                
                if len(all_questions) < 4:
                    logger.error("Not enough trivia questions in database")
                    return
                
                # Mark these as unused so we can use them
                for q in all_questions:
                    q.question_done = False
                
                unused_questions = all_questions
        
            # Allocate questions
            daily_questions = []
            for i, q in enumerate(unused_questions):
                dq = DailyQuestion(
                    question_number=q.question_number,
                    date=today,
                    is_common=(i == 0),  # First question is common
                    question_order=i + 1,
                    is_used=(i == 0),  # Common question (order 1) is always marked as used
                    correct_answer=q.correct_answer
                )
                db.add(dq)
                daily_questions.append(dq)
                
                # Mark question as used
                q.question_done = True
                q.que_displayed_date = datetime.utcnow()
        
            db.commit()
            logger.info(f"Successfully allocated {len(daily_questions)} questions for {today}")
    except Exception as e:
        logger.error(f"Error in reset_daily_questions: {str(e)}", exc_info=True)

def reset_question_usage_flags():
    """Reset question 'used' flags at 5 AM daily"""
    logger.info("Running scheduled task: reset_question_usage_flags")
    try:
        with get_session() as db:
            # Get today's date
            today = datetime.utcnow().date()
            
            # Get daily questions for today
            daily_questions = db.query(DailyQuestion).filter(
                DailyQuestion.date == today
            ).all()
            
            for question in daily_questions:
                # Reset is_used flag for non-common questions
                if not question.is_common:
                    question.is_used = False
                    logger.info(f"Reset is_used flag for question {question.question_number}")
            
            db.commit()
            logger.info(f"Successfully reset question usage flags for {today}")
    except Exception as e:
        logger.error(f"Error in reset_question_usage_flags: {str(e)}", exc_info=True)

def reset_boost_usage_flags():
    """Reset all users' daily boost usage flags"""
    logger.info("Running scheduled task: reset_boost_usage_flags")
    try:
        with get_session() as db:
            # Reset all users' daily boost usage flags
            users = db.query(User).all()
            
            for user in users:
                user.hint_used_today = False
                user.fifty_fifty_used_today = False
                user.auto_answer_used_today = False
            
            db.commit()
            logger.info(f"Successfully reset boost usage flags for {len(users)} users")
    except Exception as e:
        logger.error(f"Error in reset_boost_usage_flags: {str(e)}", exc_info=True)

def run_daily_draw():
    """Perform the daily trivia draw"""
    logger.info("Running scheduled task: run_daily_draw")
    try:
        with get_session() as db:
            # Get today's date
            today = datetime.utcnow().date()
            
            # Check if draw already performed for today
            existing_draw = db.query(TriviaDrawWinner).filter(
                TriviaDrawWinner.draw_date == today
            ).first()
            
            if existing_draw:
                logger.info(f"Draw already performed for {today}, skipping")
                return
                
            # Check if automatic draws are enabled
            config = db.query(TriviaDrawConfig).order_by(TriviaDrawConfig.id.desc()).first()
            if config and not config.automatic_draws:
                logger.info("Automatic draws are disabled in configuration, skipping")
                return
            
            # Perform the draw
            draw_result = perform_draw(db, today)
            logger.info(f"Daily draw completed with status: {draw_result['status']}")
            logger.info(f"Total participants: {draw_result['total_participants']}, Total winners: {draw_result['total_winners']}")
    except Exception as e:
        logger.error(f"Error in run_daily_draw: {str(e)}", exc_info=True)

def get_configured_draw_time():
    """Get the draw time from the database configuration"""
    try:
        with get_session() as db:
            config = db.query(TriviaDrawConfig).order_by(TriviaDrawConfig.id.desc()).first()
            
            # Default values if no config is found
            hour = 23
            minute = 0
            timezone_str = "US/Eastern"
            
            if config:
                # Use configuration values if available
                hour = config.draw_time_hour if config.draw_time_hour is not None else hour
                minute = config.draw_time_minute if config.draw_time_minute is not None else minute
                timezone_str = config.draw_timezone if config.draw_timezone else timezone_str
            
            # Convert to UTC for scheduler
            timezone = pytz.timezone(timezone_str)
            utc = pytz.UTC
            local_time = timezone.localize(datetime.combine(datetime.now().date(), 
                                                         datetime.strptime(f"{hour}:{minute}", "%H:%M").time()))
            utc_time = local_time.astimezone(utc)
            
            logger.info(f"Configured draw time: {hour}:{minute} {timezone_str} (UTC: {utc_time.hour}:{utc_time.minute})")
            return utc_time.hour, utc_time.minute
            
    except Exception as e:
        logger.error(f"Error getting configured draw time: {str(e)}", exc_info=True)
        # Fallback to default: 11 PM EST
        return 3, 0  # 11 PM EST is typically 3 AM UTC

def update_draw_scheduler():
    """
    Update the draw scheduler with the latest configuration.
    This can be called when an admin changes the draw time.
    """
    try:
        # Get the configured draw time
        draw_hour, draw_minute = get_configured_draw_time()
        
        # Remove existing job if it exists
        if scheduler.get_job('run_daily_draw'):
            scheduler.remove_job('run_daily_draw')
            logger.info("Removed existing draw scheduler job")
        
        # Add job with new configuration
        scheduler.add_job(
            run_daily_draw,
            CronTrigger(hour=draw_hour, minute=draw_minute, second=0),
            id='run_daily_draw',
            replace_existing=True
        )
        
        logger.info(f"Updated draw scheduler to run at {draw_hour}:{draw_minute} UTC")
        return True
    except Exception as e:
        logger.error(f"Error updating draw scheduler: {str(e)}", exc_info=True)
        return False

# ========= Scheduler Setup =========

def start_scheduler():
    """Initialize and start the scheduler"""
    try:
        # Schedule daily reset tasks
        # Note: Using UTC time
        
        # Reset daily questions at midnight UTC
        scheduler.add_job(
            reset_daily_questions,
            CronTrigger(hour=0, minute=0, second=0),
            id='reset_daily_questions',
            replace_existing=True
        )
        
        # Reset question usage flags at 5 AM UTC
        scheduler.add_job(
            reset_question_usage_flags,
            CronTrigger(hour=5, minute=0, second=0),
            id='reset_question_usage_flags',
            replace_existing=True
        )
        
        # Reset boost usage flags at midnight UTC
        scheduler.add_job(
            reset_boost_usage_flags,
            CronTrigger(hour=0, minute=0, second=0),
            id='reset_boost_usage_flags',
            replace_existing=True
        )
        
        # Run daily draw at the configured time (converted to UTC)
        draw_hour, draw_minute = get_configured_draw_time()
        
        scheduler.add_job(
            run_daily_draw,
            CronTrigger(hour=draw_hour, minute=draw_minute, second=0),
            id='run_daily_draw',
            replace_existing=True
        )
        
        # Start the scheduler
        scheduler.start()
        logger.info("Scheduler started successfully")
    except Exception as e:
        logger.error(f"Error starting scheduler: {str(e)}", exc_info=True) 