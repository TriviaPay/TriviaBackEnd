import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from db import SessionLocal
from rewards_logic import perform_draw, reset_daily_eligibility_flags
from cleanup_unused_questions import cleanup_unused_questions
from models import User, TriviaQuestionsDaily, TriviaQuestionsAnswers, TriviaQuestionsWinners, UserSubscription

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler = AsyncIOScheduler()

def get_detailed_draw_metrics(db: Session, draw_date: date) -> dict:
    """
    Get comprehensive metrics for the draw including participant breakdowns.
    """
    try:
        # Total users in system
        total_users = db.query(User).count()
        
        # Users with subscription flag
        subscribed_users = db.query(User).filter(User.subscription_flag == True).count()
        
        # Users eligible for draw (answered all questions correctly)
        eligible_users = db.query(User).filter(User.daily_eligibility_flag == True).count()
        
        # Users who attempted questions today
        attempted_users = db.query(User).join(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= datetime.combine(draw_date, datetime.min.time()),
            TriviaQuestionsDaily.date < datetime.combine(draw_date + timedelta(days=1), datetime.min.time()),
            TriviaQuestionsDaily.user_attempted == True
        ).distinct().count()
        
        # Users who answered all 3 questions correctly
        correct_all_questions = db.query(User).join(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= datetime.combine(draw_date, datetime.min.time()),
            TriviaQuestionsDaily.date < datetime.combine(draw_date + timedelta(days=1), datetime.min.time()),
            TriviaQuestionsDaily.user_is_correct == True
        ).group_by(User.account_id).having(func.count(TriviaQuestionsDaily.id) == 3).count()
        
        # Users who answered some questions correctly
        correct_some_questions = db.query(User).join(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= datetime.combine(draw_date, datetime.min.time()),
            TriviaQuestionsDaily.date < datetime.combine(draw_date + timedelta(days=1), datetime.min.time()),
            TriviaQuestionsDaily.user_is_correct == True
        ).distinct().count()
        
        # Users who answered all questions incorrectly
        incorrect_all_questions = db.query(User).join(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= datetime.combine(draw_date, datetime.min.time()),
            TriviaQuestionsDaily.date < datetime.combine(draw_date + timedelta(days=1), datetime.min.time()),
            TriviaQuestionsDaily.user_is_correct == False
        ).group_by(User.account_id).having(func.count(TriviaQuestionsDaily.id) == 3).count()
        
        # Combination metrics
        eligible_and_subscribed = db.query(User).filter(
            User.daily_eligibility_flag == True,
            User.subscription_flag == True
        ).count()
        
        eligible_not_subscribed = db.query(User).filter(
            User.daily_eligibility_flag == True,
            User.subscription_flag == False
        ).count()
        
        # Question attempt metrics
        total_question_attempts = db.query(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= datetime.combine(draw_date, datetime.min.time()),
            TriviaQuestionsDaily.date < datetime.combine(draw_date + timedelta(days=1), datetime.min.time()),
            TriviaQuestionsDaily.user_attempted == True
        ).count()
        
        correct_attempts = db.query(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= datetime.combine(draw_date, datetime.min.time()),
            TriviaQuestionsDaily.date < datetime.combine(draw_date + timedelta(days=1), datetime.min.time()),
            TriviaQuestionsDaily.user_is_correct == True
        ).count()
        
        incorrect_attempts = db.query(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= datetime.combine(draw_date, datetime.min.time()),
            TriviaQuestionsDaily.date < datetime.combine(draw_date + timedelta(days=1), datetime.min.time()),
            TriviaQuestionsDaily.user_is_correct == False
        ).count()
        
        # Calculate accuracy rate
        accuracy_rate = (correct_attempts / total_question_attempts * 100) if total_question_attempts > 0 else 0
        
        # Check if draw already exists
        existing_draw = db.query(TriviaQuestionsWinners).filter(
            TriviaQuestionsWinners.draw_date == draw_date
        ).first()
        
        return {
            "draw_date": draw_date.isoformat(),
            "total_users_in_system": total_users,
            "subscribed_users": subscribed_users,
            "eligible_users": eligible_users,
            "attempted_users": attempted_users,
            "correct_all_questions": correct_all_questions,
            "correct_some_questions": correct_some_questions,
            "incorrect_all_questions": incorrect_all_questions,
            "eligible_and_subscribed": eligible_and_subscribed,
            "eligible_not_subscribed": eligible_not_subscribed,
            "total_question_attempts": total_question_attempts,
            "correct_attempts": correct_attempts,
            "incorrect_attempts": incorrect_attempts,
            "accuracy_rate_percent": round(accuracy_rate, 2),
            "draw_already_performed": existing_draw is not None,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting detailed draw metrics: {str(e)}")
        return {"error": str(e)}

def get_detailed_reset_metrics(db: Session) -> dict:
    """
    Get comprehensive metrics for the question reset process.
    """
    try:
        # Count users with eligibility flags before reset
        users_with_eligibility_before = db.query(User).filter(User.daily_eligibility_flag == True).count()
        
        # Count total questions allocated today
        today = date.today()
        questions_allocated_today = db.query(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= datetime.combine(today, datetime.min.time()),
            TriviaQuestionsDaily.date < datetime.combine(today + timedelta(days=1), datetime.min.time())
        ).count()
        
        # Count questions attempted today
        questions_attempted_today = db.query(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= datetime.combine(today, datetime.min.time()),
            TriviaQuestionsDaily.date < datetime.combine(today + timedelta(days=1), datetime.min.time()),
            TriviaQuestionsDaily.user_attempted == True
        ).count()
        
        # Count questions answered correctly today
        questions_correct_today = db.query(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= datetime.combine(today, datetime.min.time()),
            TriviaQuestionsDaily.date < datetime.combine(today + timedelta(days=1), datetime.min.time()),
            TriviaQuestionsDaily.user_is_correct == True
        ).count()
        
        # Count questions answered incorrectly today
        questions_incorrect_today = db.query(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= datetime.combine(today, datetime.min.time()),
            TriviaQuestionsDaily.date < datetime.combine(today + timedelta(days=1), datetime.min.time()),
            TriviaQuestionsDaily.user_is_correct == False
        ).count()
        
        # Count unused questions (allocated but not attempted)
        unused_questions = questions_allocated_today - questions_attempted_today
        
        return {
            "reset_date": today.isoformat(),
            "users_with_eligibility_before_reset": users_with_eligibility_before,
            "questions_allocated_today": questions_allocated_today,
            "questions_attempted_today": questions_attempted_today,
            "questions_correct_today": questions_correct_today,
            "questions_incorrect_today": questions_incorrect_today,
            "unused_questions": unused_questions,
            "questions_utilization_rate": round((questions_attempted_today / questions_allocated_today * 100), 2) if questions_allocated_today > 0 else 0,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting detailed reset metrics: {str(e)}")
        return {"error": str(e)}

def get_detailed_monthly_reset_metrics(db: Session) -> dict:
    """
    Get comprehensive metrics for the monthly subscription reset process.
    """
    try:
        # Count users with subscription flags before reset
        users_with_subscription_before = db.query(User).filter(User.subscription_flag == True).count()
        
        # Count active subscriptions in UserSubscription table
        active_subscriptions = db.query(UserSubscription).filter(
            UserSubscription.status == 'active'
        ).count()
        
        # Count total subscriptions (all statuses)
        total_subscriptions = db.query(UserSubscription).count()
        
        # Count subscriptions by status
        subscription_status_counts = db.query(
            UserSubscription.status,
            func.count(UserSubscription.id)
        ).group_by(UserSubscription.status).all()
        
        # Count users who will be affected by reset
        users_to_reset = users_with_subscription_before
        
        return {
            "reset_date": date.today().isoformat(),
            "users_with_subscription_before_reset": users_with_subscription_before,
            "active_subscriptions": active_subscriptions,
            "total_subscriptions": total_subscriptions,
            "users_to_reset": users_to_reset,
            "subscription_status_breakdown": dict(subscription_status_counts),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting detailed monthly reset metrics: {str(e)}")
        return {"error": str(e)}

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
        logger.info(f"🎯 Starting daily draw at {datetime.now()}")
        db: Session = SessionLocal()
        
        try:
            # Process yesterday's draw
            yesterday = date.today() - timedelta(days=1)
            
            # Get detailed metrics before performing draw
            logger.info("📊 Collecting detailed draw metrics...")
            metrics = get_detailed_draw_metrics(db, yesterday)
            
            # Log comprehensive metrics
            logger.info("=" * 80)
            logger.info("📈 DAILY DRAW METRICS")
            logger.info("=" * 80)
            logger.info(f"📅 Draw Date: {metrics['draw_date']}")
            logger.info(f"👥 Total Users in System: {metrics['total_users_in_system']}")
            logger.info(f"💎 Subscribed Users: {metrics['subscribed_users']}")
            logger.info(f"✅ Eligible Users (answered all correctly): {metrics['eligible_users']}")
            logger.info(f"🎯 Attempted Users: {metrics['attempted_users']}")
            logger.info(f"🏆 Correct All Questions: {metrics['correct_all_questions']}")
            logger.info(f"📝 Correct Some Questions: {metrics['correct_some_questions']}")
            logger.info(f"❌ Incorrect All Questions: {metrics['incorrect_all_questions']}")
            logger.info(f"💎✅ Eligible AND Subscribed: {metrics['eligible_and_subscribed']}")
            logger.info(f"✅💎 Eligible NOT Subscribed: {metrics['eligible_not_subscribed']}")
            logger.info(f"📊 Total Question Attempts: {metrics['total_question_attempts']}")
            logger.info(f"✅ Correct Attempts: {metrics['correct_attempts']}")
            logger.info(f"❌ Incorrect Attempts: {metrics['incorrect_attempts']}")
            logger.info(f"📈 Accuracy Rate: {metrics['accuracy_rate_percent']}%")
            logger.info(f"🔄 Draw Already Performed: {metrics['draw_already_performed']}")
            logger.info("=" * 80)
            
            # Perform the actual draw
            logger.info("🎲 Performing draw...")
            result = perform_draw(db, yesterday)
            
            if result["status"] == "success":
                logger.info("🎉 DRAW COMPLETED SUCCESSFULLY!")
                logger.info(f"🏆 Winners Selected: {result['total_winners']}")
                logger.info(f"👥 Total Participants: {result['total_participants']}")
                logger.info(f"💰 Prize Pool: ${result['prize_pool']}")
                logger.info(f"📊 Winner Distribution:")
                for i, winner in enumerate(result.get('winners', []), 1):
                    logger.info(f"   {i}. {winner.get('username', 'Unknown')} - ${winner.get('prize_amount', 0)}")
            else:
                logger.warning(f"⚠️ Draw result: {result['status']} - {result.get('message', '')}")
                
        except Exception as db_error:
            logger.error(f"💥 Database error during draw: {str(db_error)}")
        finally:
            db.close()
        
    except Exception as e:
        logger.error(f"💥 Error running daily draw: {str(e)}")

async def reset_daily_questions():
    """
    Reset daily questions at 8:01 PM EST.
    This makes new questions available for the next 24-hour period.
    """
    try:
        logger.info(f"🔄 Starting daily question reset at {datetime.now()}")
        db: Session = SessionLocal()
        
        try:
            # Get detailed metrics before reset
            logger.info("📊 Collecting detailed reset metrics...")
            metrics = get_detailed_reset_metrics(db)
            
            # Log comprehensive metrics
            logger.info("=" * 80)
            logger.info("🔄 DAILY QUESTION RESET METRICS")
            logger.info("=" * 80)
            logger.info(f"📅 Reset Date: {metrics['reset_date']}")
            logger.info(f"✅ Users with Eligibility Before Reset: {metrics['users_with_eligibility_before_reset']}")
            logger.info(f"📝 Questions Allocated Today: {metrics['questions_allocated_today']}")
            logger.info(f"🎯 Questions Attempted Today: {metrics['questions_attempted_today']}")
            logger.info(f"✅ Questions Correct Today: {metrics['questions_correct_today']}")
            logger.info(f"❌ Questions Incorrect Today: {metrics['questions_incorrect_today']}")
            logger.info(f"📊 Unused Questions: {metrics['unused_questions']}")
            logger.info(f"📈 Questions Utilization Rate: {metrics['questions_utilization_rate']}%")
            logger.info("=" * 80)
            
            # Clean up unused questions from today
            logger.info("🧹 Cleaning up unused questions...")
            cleanup_unused_questions()
            
            # Reset eligibility flags for new day
            logger.info("🔄 Resetting eligibility flags...")
            reset_daily_eligibility_flags(db)
            
            logger.info("✅ Successfully completed daily question reset!")
            
        except Exception as e:
            logger.error(f"💥 Error during question reset: {e}")
        finally:
            db.close()
        
    except Exception as e:
        logger.error(f"💥 Error resetting daily questions: {str(e)}")

async def run_monthly_subscription_reset():
    """
    Reset monthly subscription flags at 11:59 PM EST on the last day of each month.
    """
    try:
        logger.info(f"📅 Starting monthly subscription reset at {datetime.now()}")
        db: Session = SessionLocal()
        
        try:
            # Get detailed metrics before reset
            logger.info("📊 Collecting detailed monthly reset metrics...")
            metrics = get_detailed_monthly_reset_metrics(db)
            
            # Log comprehensive metrics
            logger.info("=" * 80)
            logger.info("📅 MONTHLY SUBSCRIPTION RESET METRICS")
            logger.info("=" * 80)
            logger.info(f"📅 Reset Date: {metrics['reset_date']}")
            logger.info(f"💎 Users with Subscription Before Reset: {metrics['users_with_subscription_before_reset']}")
            logger.info(f"🟢 Active Subscriptions: {metrics['active_subscriptions']}")
            logger.info(f"📊 Total Subscriptions: {metrics['total_subscriptions']}")
            logger.info(f"🔄 Users to Reset: {metrics['users_to_reset']}")
            logger.info("📈 Subscription Status Breakdown:")
            for status, count in metrics['subscription_status_breakdown'].items():
                logger.info(f"   {status}: {count}")
            logger.info("=" * 80)
            
            # Reset all subscription flags
            logger.info("🔄 Resetting subscription flags...")
            db.query(User).update({"subscription_flag": False})
            db.commit()
            
            logger.info(f"✅ Successfully reset {metrics['users_to_reset']} subscription flags!")
            
        except Exception as db_error:
            db.rollback()
            logger.error(f"💥 Database error during subscription reset: {str(db_error)}")
        finally:
            db.close()
        
    except Exception as e:
        logger.error(f"💥 Error running monthly subscription reset: {str(e)}")

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
