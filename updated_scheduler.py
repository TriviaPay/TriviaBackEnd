import logging
import random
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, Integer, cast, select
from db import SessionLocal
from rewards_logic import perform_draw, reset_daily_eligibility_flags, reset_weekly_daily_rewards
from cleanup_unused_questions import cleanup_unused_questions
from models import (
    User, TriviaQuestionsDaily, TriviaQuestionsWinners, UserSubscription, TriviaUserDaily, TriviaQuestionsEntries
)
from models import (
    TriviaModeConfig, TriviaQuestionsFreeMode, TriviaQuestionsFreeModeDaily, TriviaFreeModeWinners,
    TriviaQuestionsFiveDollarMode, TriviaQuestionsFiveDollarModeDaily, TriviaFiveDollarModeWinners
)
from utils.free_mode_rewards import (
    get_eligible_participants_free_mode, rank_participants_by_completion,
    calculate_reward_distribution, distribute_rewards_to_winners, cleanup_old_leaderboard
)
from utils.five_dollar_mode_service import (
    get_eligible_participants_five_dollar_mode, rank_participants_by_submission_time,
    calculate_total_pool_five_dollar_mode, distribute_rewards_to_winners_five_dollar_mode,
    cleanup_old_leaderboard_five_dollar_mode
)
from utils.trivia_mode_service import get_mode_config, get_active_draw_date, get_date_range_for_query
from utils.mode_draw_service import register_mode_handler, execute_mode_draw

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
        
        # Users eligible for draw (answered at least 1 question correctly on draw_date)
        # This should match the logic in get_eligible_participants()
        eligible_users = db.query(
            func.count(func.distinct(User.account_id))
        ).join(
            TriviaUserDaily,
            and_(
                TriviaUserDaily.account_id == User.account_id,
                TriviaUserDaily.date == draw_date,
                TriviaUserDaily.status == 'answered_correct'
            )
        ).filter(
            User.subscription_flag == True
        ).scalar() or 0
        
        # Users who attempted questions on draw_date (using TriviaUserDaily)
        # Count distinct users who attempted at least one question
        attempted_users = db.query(
            func.count(func.distinct(TriviaUserDaily.account_id))
        ).filter(
            TriviaUserDaily.date == draw_date,
            TriviaUserDaily.status.in_(['answered_correct', 'answered_wrong', 'viewed'])
        ).scalar() or 0
        
        # Users who answered all questions correctly (assumes max 4 questions per day)
        # Count users who have exactly 4 correct answers (or check if all their questions are correct)
        users_with_correct = db.query(
            TriviaUserDaily.account_id,
            func.count(TriviaUserDaily.account_id).label('correct_count'),
            func.count(func.distinct(TriviaUserDaily.question_order)).label('total_attempted')
        ).filter(
            TriviaUserDaily.date == draw_date,
            TriviaUserDaily.status == 'answered_correct'
        ).group_by(TriviaUserDaily.account_id).all()
        
        correct_all_questions = sum(
            1 for _, correct_count, total_attempted in users_with_correct
            if correct_count >= 4  # Answered all 4 questions correctly
        )
        
        # Users who answered some questions correctly (at least 1)
        correct_some_questions = db.query(
            func.count(func.distinct(TriviaUserDaily.account_id))
        ).filter(
            TriviaUserDaily.date == draw_date,
            TriviaUserDaily.status == 'answered_correct'
        ).scalar() or 0
        
        # Users who answered all questions incorrectly
        # Count users who attempted questions but got 0 correct answers on draw_date
        # Approach: Get all users who attempted, then subtract users who got at least one correct
        all_users_who_attempted = set(
            row[0] for row in db.query(
                func.distinct(TriviaUserDaily.account_id)
            ).filter(
                TriviaUserDaily.date == draw_date,
                TriviaUserDaily.status.in_(['answered_correct', 'answered_wrong'])
            ).all()
        )
        
        users_who_got_correct = set(
            row[0] for row in db.query(
                func.distinct(TriviaUserDaily.account_id)
            ).filter(
                TriviaUserDaily.date == draw_date,
                TriviaUserDaily.status == 'answered_correct'
            ).all()
        )
        
        # Users who attempted but got no correct answers
        incorrect_all_questions = len(all_users_who_attempted - users_who_got_correct)
        
        # Combination metrics (based on actual draw_date data)
        # Eligible AND subscribed (this is what get_eligible_participants returns)
        eligible_and_subscribed = db.query(
            func.count(func.distinct(User.account_id))
        ).join(
            TriviaUserDaily,
            and_(
                TriviaUserDaily.account_id == User.account_id,
                TriviaUserDaily.date == draw_date,
                TriviaUserDaily.status == 'answered_correct'
            )
        ).filter(
            User.subscription_flag == True
        ).scalar() or 0
        
        # Eligible but NOT subscribed (answered correctly but not subscribed)
        eligible_not_subscribed = db.query(
            func.count(func.distinct(User.account_id))
        ).join(
            TriviaUserDaily,
            and_(
                TriviaUserDaily.account_id == User.account_id,
                TriviaUserDaily.date == draw_date,
                TriviaUserDaily.status == 'answered_correct'
            )
        ).filter(
            User.subscription_flag == False
        ).scalar() or 0
        
        # Question attempt metrics (using TriviaUserDaily)
        total_question_attempts = db.query(TriviaUserDaily).filter(
            TriviaUserDaily.date == draw_date,
            TriviaUserDaily.status.in_(['answered_correct', 'answered_wrong'])
        ).count()
        
        correct_attempts = db.query(TriviaUserDaily).filter(
            TriviaUserDaily.date == draw_date,
            TriviaUserDaily.status == 'answered_correct'
        ).count()
        
        incorrect_attempts = db.query(TriviaUserDaily).filter(
            TriviaUserDaily.date == draw_date,
            TriviaUserDaily.status == 'answered_wrong'
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
        
        # Count questions attempted today (using TriviaUserDaily)
        questions_attempted_today = db.query(TriviaUserDaily).filter(
            TriviaUserDaily.date == today,
            TriviaUserDaily.status.in_(['answered_correct', 'answered_wrong', 'viewed'])
        ).count()
        
        # Count questions answered correctly today
        questions_correct_today = db.query(TriviaUserDaily).filter(
            TriviaUserDaily.date == today,
            TriviaUserDaily.status == 'answered_correct'
        ).count()
        
        # Count questions answered incorrectly today
        questions_incorrect_today = db.query(TriviaUserDaily).filter(
            TriviaUserDaily.date == today,
            TriviaUserDaily.status == 'answered_wrong'
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
        "hour": int(os.environ.get("DRAW_TIME_HOUR", "18")),  # Default 6 PM
        "minute": int(os.environ.get("DRAW_TIME_MINUTE", "0")),  # Default 0 minutes
        "timezone": os.environ.get("DRAW_TIMEZONE", "US/Eastern")  # Default EST
    }

def schedule_draws():
    """
    Schedule the daily draw and question reset.
    
    Timing (configurable via DRAW_TIME_HOUR, DRAW_TIME_MINUTE, DRAW_TIMEZONE):
    - Draw time (default 6:00 PM EST): Process yesterday's draw (winners selected)
    - Draw time + 1 minute (default 6:01 PM EST): Reset questions and eligibility flags for new day
    - Questions available from reset time to next draw time
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
    
    # Schedule weekly daily rewards reset job (Monday at 00:00)
    scheduler.add_job(
        run_weekly_rewards_reset,
        CronTrigger(day_of_week="mon", hour=0, minute=0, timezone=timezone),
        id="weekly_rewards_reset",
        replace_existing=True,
        misfire_grace_time=3600
    )
    
    # Schedule free mode draw job (same time as regular draw)
    scheduler.add_job(
        run_free_mode_draw,
        CronTrigger(hour=hour, minute=minute, timezone=timezone),
        id="free_mode_draw",
        replace_existing=True,
        misfire_grace_time=3600
    )
    
    # Schedule free mode question allocation (1 minute after draw, same as regular questions)
    scheduler.add_job(
        allocate_free_mode_questions,
        CronTrigger(hour=hour, minute=minute+1, timezone=timezone),
        id="free_mode_question_allocation",
        replace_existing=True,
        misfire_grace_time=3600
    )
    
    # Schedule $5 mode draw job (same time as regular draw)
    scheduler.add_job(
        run_five_dollar_mode_draw,
        CronTrigger(hour=hour, minute=minute, timezone=timezone),
        id="five_dollar_mode_draw",
        replace_existing=True,
        misfire_grace_time=3600
    )
    
    # Schedule $5 mode question allocation (1 minute after draw, same as regular questions)
    scheduler.add_job(
        allocate_five_dollar_mode_questions,
        CronTrigger(hour=hour, minute=minute+1, timezone=timezone),
        id="five_dollar_mode_question_allocation",
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
    Reset daily questions at draw time + 1 minute (default 6:01 PM EST).
    This makes new questions available for the next 24-hour period.
    The reset time is automatically set to 1 minute after the configured draw time.
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

async def run_weekly_rewards_reset():
    """
    Reset weekly daily rewards at Monday 00:00 (midnight) in the configured timezone.
    """
    try:
        logger.info(f"📅 Starting weekly daily rewards reset at {datetime.now()}")
        db: Session = SessionLocal()
        
        try:
            # Reset weekly daily rewards
            logger.info("🔄 Resetting weekly daily rewards...")
            reset_weekly_daily_rewards(db)
            
            logger.info("✅ Successfully completed weekly daily rewards reset!")
            
        except Exception as e:
            logger.error(f"💥 Error during weekly rewards reset: {e}")
        finally:
            db.close()
        
    except Exception as e:
        logger.error(f"💥 Error resetting weekly daily rewards: {str(e)}")

async def run_free_mode_draw():
    """
    Process free mode draw at the configured draw time.
    Calculates winners, distributes gems, and cleans up old leaderboard.
    """
    try:
        logger.info(f"🎯 Starting free mode draw at {datetime.now()}")
        db: Session = SessionLocal()
        
        try:
            # Process yesterday's draw
            yesterday = date.today() - timedelta(days=1)
            
            # Check if draw already performed
            existing_draw = db.query(TriviaFreeModeWinners).filter(
                TriviaFreeModeWinners.draw_date == yesterday
            ).first()
            
            if existing_draw:
                logger.info(f"⏭️ Draw for {yesterday} has already been performed, skipping...")
                return
            
            # Get mode config
            mode_config = get_mode_config(db, 'free_mode')
            if not mode_config:
                logger.warning("⚠️ Free mode config not found, skipping draw...")
                return
            
            # Get eligible participants
            participants = get_eligible_participants_free_mode(db, yesterday)
            
            if not participants:
                logger.info(f"📭 No eligible participants for draw on {yesterday}")
                return
            
            logger.info(f"👥 Found {len(participants)} eligible participants")
            
            # Rank participants
            ranked_participants = rank_participants_by_completion(participants)
            
            # Calculate reward distribution
            reward_info = calculate_reward_distribution(mode_config, len(ranked_participants))
            winner_count = reward_info['winner_count']
            gem_amounts = reward_info['gem_amounts']
            
            # Select winners
            if len(ranked_participants) <= winner_count:
                winners_list = ranked_participants
            else:
                winners_list = ranked_participants[:winner_count]
            
            # Prepare winners with gem amounts
            winners = []
            for i, participant in enumerate(winners_list):
                winners.append({
                    'account_id': participant['account_id'],
                    'username': participant['username'],
                    'position': i + 1,
                    'gems_awarded': gem_amounts[i] if i < len(gem_amounts) else 0,
                    'completed_at': participant['third_question_completed_at']
                })
            
            # Distribute rewards
            distribution_result = distribute_rewards_to_winners(db, winners, mode_config, yesterday)
            
            # Cleanup old leaderboard (previous draw date)
            previous_draw_date = yesterday - date.resolution
            cleanup_old_leaderboard(db, previous_draw_date)
            
            logger.info("🎉 FREE MODE DRAW COMPLETED SUCCESSFULLY!")
            logger.info(f"🏆 Winners Selected: {len(winners)}")
            logger.info(f"👥 Total Participants: {len(ranked_participants)}")
            logger.info(f"💎 Total Gems Awarded: {distribution_result['total_gems_awarded']}")
            
        except Exception as db_error:
            db.rollback()
            logger.error(f"💥 Database error during free mode draw: {str(db_error)}")
        finally:
            db.close()
        
    except Exception as e:
        logger.error(f"💥 Error running free mode draw: {str(e)}")

async def allocate_free_mode_questions():
    """
    Allocate free mode questions for the new day.
    Selects random questions from TriviaQuestionsFreeMode and adds them to TriviaQuestionsFreeModeDaily.
    """
    try:
        logger.info(f"🔄 Starting free mode question allocation at {datetime.now()}")
        db: Session = SessionLocal()
        
        try:
            # Get mode config
            mode_config = get_mode_config(db, 'free_mode')
            if not mode_config:
                logger.warning("⚠️ Free mode config not found, skipping question allocation...")
                return
            
            questions_count = mode_config.questions_count
            target_date = get_active_draw_date()
            
            # Get date range for the target date
            start_datetime, end_datetime = get_date_range_for_query(target_date)
            
            # Check if questions already allocated for this date
            existing_questions = db.query(TriviaQuestionsFreeModeDaily).filter(
                TriviaQuestionsFreeModeDaily.date >= start_datetime,
                TriviaQuestionsFreeModeDaily.date <= end_datetime
            ).count()
            
            if existing_questions > 0:
                logger.info(f"⏭️ Questions already allocated for {target_date}, skipping...")
                return
            
            # Get available questions (not used recently, prefer unused)
            unused_questions = db.query(TriviaQuestionsFreeMode).filter(
                TriviaQuestionsFreeMode.is_used == False
            ).all()
            
            # If not enough unused questions, get any questions
            if len(unused_questions) < questions_count:
                all_questions = db.query(TriviaQuestionsFreeMode).all()
                available_questions = random.sample(all_questions, min(questions_count, len(all_questions)))
            else:
                available_questions = random.sample(unused_questions, questions_count)
            
            if len(available_questions) < questions_count:
                logger.warning(f"⚠️ Only {len(available_questions)} questions available, need {questions_count}")
            
            # Allocate questions to daily pool
            allocated_count = 0
            for i, question in enumerate(available_questions[:questions_count], 1):
                daily_question = TriviaQuestionsFreeModeDaily(
                    date=start_datetime,
                    question_id=question.id,
                    question_order=i,
                    is_used=False
                )
                db.add(daily_question)
                # Mark question as used
                question.is_used = True
                allocated_count += 1
            
            db.commit()
            logger.info(f"✅ Successfully allocated {allocated_count} questions for {target_date}")
            
        except Exception as db_error:
            db.rollback()
            logger.error(f"💥 Database error during question allocation: {str(db_error)}")
        finally:
            db.close()
        
    except Exception as e:
        logger.error(f"💥 Error allocating free mode questions: {str(e)}")

async def run_five_dollar_mode_draw():
    """
    Process $5 mode draw at the configured draw time.
    Uses generic draw service with registered handlers.
    """
    try:
        logger.info(f"🎯 Starting $5 mode draw at {datetime.now()}")
        db: Session = SessionLocal()
        
        try:
            # Process yesterday's draw
            yesterday = date.today() - timedelta(days=1)
            
            # Check if draw already performed
            existing_draw = db.query(TriviaFiveDollarModeWinners).filter(
                TriviaFiveDollarModeWinners.draw_date == yesterday
            ).first()
            
            if existing_draw:
                logger.info(f"⏭️ Draw for {yesterday} has already been performed, skipping...")
                return
            
            # Execute draw using generic service
            result = execute_mode_draw(db, 'five_dollar_mode', yesterday)
            
            if result['status'] == 'no_participants':
                logger.info(f"📭 No eligible participants for $5 mode draw on {yesterday}")
                return
            
            if result['status'] != 'success':
                logger.error(f"❌ Draw failed: {result.get('message', 'Unknown error')}")
                return
            
            # Distribute rewards
            mode_config = get_mode_config(db, 'five_dollar_mode')
            if mode_config:
                winners = result.get('winners', [])
                distribution_result = distribute_rewards_to_winners_five_dollar_mode(
                    db, winners, mode_config, yesterday
                )
                
                # Cleanup old leaderboard
                previous_draw_date = yesterday - date.resolution
                cleanup_old_leaderboard_five_dollar_mode(db, previous_draw_date)
                
                logger.info("🎉 $5 MODE DRAW COMPLETED SUCCESSFULLY!")
                logger.info(f"🏆 Winners Selected: {len(winners)}")
                logger.info(f"👥 Total Participants: {result.get('total_participants', 0)}")
                logger.info(f"💰 Total Money Awarded: ${distribution_result.get('total_money_awarded', 0):.2f}")
            
        except Exception as db_error:
            db.rollback()
            logger.error(f"💥 Database error during $5 mode draw: {str(db_error)}")
        finally:
            db.close()
        
    except Exception as e:
        logger.error(f"💥 Error running $5 mode draw: {str(e)}")

async def allocate_five_dollar_mode_questions():
    """
    Allocate $5 mode question for the new day.
    Selects a random question from TriviaQuestionsFiveDollarMode and adds it to TriviaQuestionsFiveDollarModeDaily.
    """
    try:
        logger.info(f"🔄 Starting $5 mode question allocation at {datetime.now()}")
        db: Session = SessionLocal()
        
        try:
            # Get mode config
            mode_config = get_mode_config(db, 'five_dollar_mode')
            if not mode_config:
                logger.warning("⚠️ $5 mode config not found, skipping question allocation...")
                return
            
            target_date = get_active_draw_date()
            
            # Get date range for the target date
            start_datetime, end_datetime = get_date_range_for_query(target_date)
            
            # Check if question already allocated for this date
            existing_question = db.query(TriviaQuestionsFiveDollarModeDaily).filter(
                TriviaQuestionsFiveDollarModeDaily.date >= start_datetime,
                TriviaQuestionsFiveDollarModeDaily.date <= end_datetime
            ).count()
            
            if existing_question > 0:
                logger.info(f"⏭️ Question already allocated for {target_date}, skipping...")
                return
            
            # Get available questions (prefer unused)
            unused_questions = db.query(TriviaQuestionsFiveDollarMode).filter(
                TriviaQuestionsFiveDollarMode.is_used == False
            ).all()
            
            # If not enough unused questions, get any questions
            import random
            if len(unused_questions) < 1:
                all_questions = db.query(TriviaQuestionsFiveDollarMode).all()
                if len(all_questions) >= 1:
                    selected_question = random.choice(all_questions)
                else:
                    logger.warning("⚠️ No questions available for $5 mode")
                    return
            else:
                selected_question = random.choice(unused_questions)
            
            # Allocate question to daily pool
            daily_question = TriviaQuestionsFiveDollarModeDaily(
                date=start_datetime,
                question_id=selected_question.id,
                question_order=1,  # Always 1 for $5 mode
                is_used=False
            )
            db.add(daily_question)
            # Mark question as used
            selected_question.is_used = True
            
            db.commit()
            logger.info(f"✅ Successfully allocated question for $5 mode on {target_date}")
            
        except Exception as db_error:
            db.rollback()
            logger.error(f"💥 Database error during $5 mode question allocation: {str(db_error)}")
        finally:
            db.close()
        
    except Exception as e:
        logger.error(f"💥 Error allocating $5 mode questions: {str(e)}")

def start_scheduler():
    """
    Start the background scheduler.
    This should be called when the application starts.
    """
    global scheduler
    
    if not scheduler.running:
        # Register mode handlers
        register_mode_handlers()
        schedule_draws()
        scheduler.start()
        logger.info("Scheduler started successfully")
    else:
        logger.warning("Scheduler is already running")


def register_mode_handlers():
    """
    Register mode-specific handlers for the generic draw service.
    """
    # Register free mode handler
    register_mode_handler(
        mode_id='free_mode',
        eligibility_func=get_eligible_participants_free_mode,
        ranking_func=rank_participants_by_completion,
        reward_calc_func=None  # Uses config value
    )
    
    # Register $5 mode handler
    register_mode_handler(
        mode_id='five_dollar_mode',
        eligibility_func=get_eligible_participants_five_dollar_mode,
        ranking_func=rank_participants_by_submission_time,
        reward_calc_func=calculate_total_pool_five_dollar_mode
    )
    
    logger.info("Mode handlers registered successfully")

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
