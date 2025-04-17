from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
from sqlalchemy import func
from contextlib import contextmanager
from datetime import datetime, timedelta
import logging
import pytz

from models import DailyQuestion, Trivia, User
from db import get_db

# Configure logging
logger = logging.getLogger(__name__)

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

# ========= Scheduler Setup =========

def start_scheduler():
    """Initialize and start the scheduler"""
    try:
        scheduler = BackgroundScheduler()
        
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
        
        # Start the scheduler
        scheduler.start()
        logger.info("Scheduler started successfully")
    except Exception as e:
        logger.error(f"Error starting scheduler: {str(e)}", exc_info=True) 