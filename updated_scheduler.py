import logging
import os
import random
from datetime import date, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import Integer, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from core.db import SessionLocal

# Legacy tables removed: TriviaQuestionsDaily, TriviaQuestionsWinners, TriviaUserDaily, TriviaQuestionsEntries
# Legacy cleanup_unused_questions removed - TriviaQuestionsDaily and Trivia tables deleted
# Legacy perform_draw removed - use mode-specific draws instead
from models import (
    TriviaBronzeModeWinners,
    TriviaFreeModeWinners,
    TriviaModeConfig,
    TriviaQuestionsBronzeMode,
    TriviaQuestionsBronzeModeDaily,
    TriviaQuestionsFreeMode,
    TriviaQuestionsFreeModeDaily,
    TriviaQuestionsSilverMode,
    TriviaQuestionsSilverModeDaily,
    TriviaSilverModeWinners,
    User,
    UserSubscription,
)
from rewards_logic import (
    calculate_prize_pool,
    reset_daily_eligibility_flags,
    reset_weekly_daily_rewards,
)
from utils.bronze_mode_service import (
    calculate_total_pool_bronze_mode,
    cleanup_old_leaderboard_bronze_mode,
    distribute_rewards_to_winners_bronze_mode,
    get_eligible_participants_bronze_mode,
    rank_participants_by_submission_time,
)
from utils.free_mode_rewards import (
    calculate_reward_distribution,
    cleanup_old_leaderboard,
    distribute_rewards_to_winners,
    get_eligible_participants_free_mode,
    rank_participants_by_completion,
)
from utils.mode_draw_service import execute_mode_draw, register_mode_handler
from utils.silver_mode_service import (
    calculate_total_pool_silver_mode,
    cleanup_old_leaderboard_silver_mode,
    distribute_rewards_to_winners_silver_mode,
    get_eligible_participants_silver_mode,
)
from utils.silver_mode_service import (
    rank_participants_by_submission_time as rank_silver_participants,
)
from utils.trivia_mode_service import (
    get_active_draw_date,
    get_date_range_for_query,
    get_mode_config,
    get_today_in_app_timezone,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global scheduler instance
scheduler = AsyncIOScheduler()


# Legacy get_detailed_draw_metrics removed - uses TriviaUserDaily, TriviaQuestionsWinners (deleted)
# Legacy get_detailed_draw_metrics removed - uses TriviaUserDaily and TriviaQuestionsWinners (deleted)
def get_detailed_draw_metrics(db: Session, draw_date: date) -> dict:
    """
    Get basic metrics for the draw.
    Legacy tables removed - returns basic metrics only.
    """
    try:
        # Total users in system
        total_users = db.query(User).count()

        # Users with subscription flag
        subscribed_users = db.query(User).filter(User.subscription_flag == True).count()

        return {
            "draw_date": draw_date.isoformat(),
            "total_users_in_system": total_users,
            "subscribed_users": subscribed_users,
            "note": "Legacy draw metrics removed - TriviaUserDaily and TriviaQuestionsWinners tables deleted",
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Error getting detailed draw metrics: {str(e)}")
        return {"error": str(e)}


# Legacy get_detailed_reset_metrics removed - uses TriviaQuestionsDaily and TriviaUserDaily (deleted)
def get_detailed_reset_metrics(db: Session) -> dict:
    """
    Get comprehensive metrics for the question reset process.
    Legacy tables removed - returns basic metrics only.
    """
    try:
        # Count users with eligibility flags before reset
        users_with_eligibility_before = (
            db.query(User).filter(User.daily_eligibility_flag == True).count()
        )

        today = date.today()

        return {
            "reset_date": today.isoformat(),
            "users_with_eligibility_before_reset": users_with_eligibility_before,
            "note": "Legacy question metrics removed - TriviaQuestionsDaily and TriviaUserDaily tables deleted",
            "timestamp": datetime.now().isoformat(),
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
        users_with_subscription_before = (
            db.query(User).filter(User.subscription_flag == True).count()
        )

        # Count active subscriptions in UserSubscription table
        active_subscriptions = (
            db.query(UserSubscription)
            .filter(UserSubscription.status == "active")
            .count()
        )

        # Count total subscriptions (all statuses)
        total_subscriptions = db.query(UserSubscription).count()

        # Count subscriptions by status
        subscription_status_counts = (
            db.query(UserSubscription.status, func.count(UserSubscription.id))
            .group_by(UserSubscription.status)
            .all()
        )

        # Count users who will be affected by reset
        users_to_reset = users_with_subscription_before

        return {
            "reset_date": date.today().isoformat(),
            "users_with_subscription_before_reset": users_with_subscription_before,
            "active_subscriptions": active_subscriptions,
            "total_subscriptions": total_subscriptions,
            "users_to_reset": users_to_reset,
            "subscription_status_breakdown": dict(subscription_status_counts),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Error getting detailed monthly reset metrics: {str(e)}")
        return {"error": str(e)}


from typing import Dict, Union


def get_draw_time() -> Dict[str, Union[int, str]]:
    """Get draw time configuration from environment variables"""
    import os

    return {
        "hour": int(os.environ.get("DRAW_TIME_HOUR", "18")),  # Default 6 PM
        "minute": int(os.environ.get("DRAW_TIME_MINUTE", "0")),  # Default 0 minutes
        "timezone": os.environ.get("DRAW_TIMEZONE", "US/Eastern"),  # Default EST
    }


def run_daily_company_revenue_update() -> None:
    """
    Update monthly company revenue using the current subscriber count.
    Scheduled once per day to avoid per-request writes.
    """
    db = SessionLocal()
    try:
        draw_date = get_today_in_app_timezone()
        calculate_prize_pool(db, draw_date, commit_revenue=True)
        logger.info(f"Company revenue updated for {draw_date}")
    except Exception as e:
        logger.error(f"Error updating company revenue: {str(e)}")
    finally:
        db.close()


def schedule_draws() -> None:
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
    reset_delay_minutes = int(os.getenv("DRAW_RESET_DELAY_MINUTES", "30"))
    reset_time = datetime(2000, 1, 1, hour, minute) + timedelta(minutes=reset_delay_minutes)
    reset_hour = reset_time.hour
    reset_minute = reset_time.minute

    logger.info(f"Scheduling daily draw at {hour}:{minute} {timezone}")
    logger.info(f"Question reset at {reset_hour}:{reset_minute} {timezone}")

    # Legacy daily draw and question reset jobs removed - use mode-specific draws instead
    # Legacy tables (TriviaQuestionsDaily, TriviaUserDaily, Trivia, TriviaQuestionsEntries) deleted

    # Schedule monthly subscription reset job (11:59 PM EST on last day of each month)
    scheduler.add_job(
        run_monthly_subscription_reset,
        CronTrigger(day="last", hour=23, minute=59, timezone=timezone),
        id="monthly_subscription_reset",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Schedule weekly daily rewards reset job (Monday at 00:00)
    scheduler.add_job(
        run_weekly_rewards_reset,
        CronTrigger(day_of_week="mon", hour=0, minute=0, timezone=timezone),
        id="weekly_rewards_reset",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Schedule daily revenue update (once per day)
    scheduler.add_job(
        run_daily_company_revenue_update,
        CronTrigger(hour=hour, minute=minute, timezone=timezone),
        id="daily_company_revenue_update",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Schedule free mode draw job (same time as regular draw)
    scheduler.add_job(
        run_free_mode_draw,
        CronTrigger(hour=hour, minute=minute, timezone=timezone),
        id="free_mode_draw",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Schedule free mode question allocation (1 minute after draw, same as regular questions)
    scheduler.add_job(
        allocate_free_mode_questions,
        CronTrigger(hour=reset_hour, minute=reset_minute, timezone=timezone),
        id="free_mode_question_allocation",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Schedule $5 mode draw job (same time as regular draw)
    scheduler.add_job(
        run_bronze_mode_draw,
        CronTrigger(hour=hour, minute=minute, timezone=timezone),
        id="bronze_mode_draw",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # Schedule $5 mode question allocation (1 minute after draw, same as regular questions)
    scheduler.add_job(
        allocate_bronze_mode_questions,
        CronTrigger(hour=reset_hour, minute=reset_minute, timezone=timezone),
        id="bronze_mode_question_allocation",
        replace_existing=True,
        misfire_grace_time=3600,
    )


# Legacy run_daily_draw removed - uses perform_draw which requires TriviaQuestionsWinners (deleted)
# Use mode-specific draw functions instead (run_free_mode_draw, run_bronze_mode_draw, etc.)

# Legacy reset_daily_questions removed - uses TriviaQuestionsDaily and Trivia tables (deleted)
# Use mode-specific question allocation instead (allocate_free_mode_questions, allocate_bronze_mode_questions, etc.)


async def run_monthly_subscription_reset() -> None:
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
            logger.info(
                f"💎 Users with Subscription Before Reset: {metrics['users_with_subscription_before_reset']}"
            )
            logger.info(f"🟢 Active Subscriptions: {metrics['active_subscriptions']}")
            logger.info(f"📊 Total Subscriptions: {metrics['total_subscriptions']}")
            logger.info(f"🔄 Users to Reset: {metrics['users_to_reset']}")
            logger.info("📈 Subscription Status Breakdown:")
            for status, count in metrics["subscription_status_breakdown"].items():
                logger.info(f"   {status}: {count}")
            logger.info("=" * 80)

            # Reset all subscription flags
            logger.info("🔄 Resetting subscription flags...")
            db.query(User).update({"subscription_flag": False})
            db.commit()

            logger.info(
                f"✅ Successfully reset {metrics['users_to_reset']} subscription flags!"
            )

        except Exception as db_error:
            db.rollback()
            logger.error(
                f"💥 Database error during subscription reset: {str(db_error)}"
            )
        finally:
            db.close()

    except Exception as e:
        logger.error(f"💥 Error running monthly subscription reset: {str(e)}")


async def run_weekly_rewards_reset() -> None:
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


async def run_free_mode_draw() -> None:
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
            existing_draw = (
                db.query(TriviaFreeModeWinners)
                .filter(TriviaFreeModeWinners.draw_date == yesterday)
                .first()
            )

            if existing_draw:
                logger.info(
                    f"⏭️ Draw for {yesterday} has already been performed, skipping..."
                )
                return

            # Get mode config
            mode_config = get_mode_config(db, "free_mode")
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
            reward_info = calculate_reward_distribution(
                mode_config, len(ranked_participants)
            )
            winner_count = reward_info["winner_count"]
            gem_amounts = reward_info["gem_amounts"]

            # Select winners
            if len(ranked_participants) <= winner_count:
                winners_list = ranked_participants
            else:
                winners_list = ranked_participants[:winner_count]

            # Prepare winners with gem amounts
            winners = []
            for i, participant in enumerate(winners_list):
                winners.append(
                    {
                        "account_id": participant["account_id"],
                        "username": participant["username"],
                        "position": i + 1,
                        "gems_awarded": gem_amounts[i] if i < len(gem_amounts) else 0,
                        "completed_at": participant["third_question_completed_at"],
                    }
                )

            # Distribute rewards
            distribution_result = distribute_rewards_to_winners(
                db, winners, mode_config, yesterday
            )

            # Cleanup old leaderboard (previous draw date)
            previous_draw_date = yesterday - date.resolution
            cleanup_old_leaderboard(db, previous_draw_date)

            logger.info("🎉 FREE MODE DRAW COMPLETED SUCCESSFULLY!")
            logger.info(f"🏆 Winners Selected: {len(winners)}")
            logger.info(f"👥 Total Participants: {len(ranked_participants)}")
            logger.info(
                f"💎 Total Gems Awarded: {distribution_result['total_gems_awarded']}"
            )

        except Exception as db_error:
            db.rollback()
            logger.error(f"💥 Database error during free mode draw: {str(db_error)}")
        finally:
            db.close()

    except Exception as e:
        logger.error(f"💥 Error running free mode draw: {str(e)}")


async def allocate_free_mode_questions() -> None:
    """
    Allocate free mode questions for the new day.
    Selects random questions from TriviaQuestionsFreeMode and adds them to TriviaQuestionsFreeModeDaily.
    """
    try:
        logger.info(f"🔄 Starting free mode question allocation at {datetime.now()}")
        db: Session = SessionLocal()

        try:
            # Get mode config
            mode_config = get_mode_config(db, "free_mode")
            if not mode_config:
                logger.warning(
                    "⚠️ Free mode config not found, skipping question allocation..."
                )
                return

            questions_count = mode_config.questions_count
            target_date = get_active_draw_date()

            # Get date range for the target date
            start_datetime, end_datetime = get_date_range_for_query(target_date)

            # Check if questions already allocated for this date
            existing_questions = (
                db.query(TriviaQuestionsFreeModeDaily)
                .filter(
                    TriviaQuestionsFreeModeDaily.date >= start_datetime,
                    TriviaQuestionsFreeModeDaily.date <= end_datetime,
                )
                .count()
            )

            if existing_questions > 0:
                logger.info(
                    f"⏭️ Questions already allocated for {target_date}, skipping..."
                )
                return

            # Get available questions (not used recently, prefer unused)
            unused_questions = (
                db.query(TriviaQuestionsFreeMode)
                .filter(TriviaQuestionsFreeMode.is_used == False)
                .all()
            )

            # If not enough unused questions, get any questions
            if len(unused_questions) < questions_count:
                all_questions = db.query(TriviaQuestionsFreeMode).all()
                available_questions = random.sample(
                    all_questions, min(questions_count, len(all_questions))
                )
            else:
                available_questions = random.sample(unused_questions, questions_count)

            if len(available_questions) < questions_count:
                logger.warning(
                    f"⚠️ Only {len(available_questions)} questions available, need {questions_count}"
                )

            # Allocate questions to daily pool
            allocated_count = 0
            for i, question in enumerate(available_questions[:questions_count], 1):
                daily_question = TriviaQuestionsFreeModeDaily(
                    date=start_datetime,
                    question_id=question.id,
                    question_order=i,
                    is_used=False,
                )
                db.add(daily_question)
                # Mark question as used
                question.is_used = True
                allocated_count += 1

            db.commit()
            logger.info(
                f"✅ Successfully allocated {allocated_count} questions for {target_date}"
            )

        except Exception as db_error:
            db.rollback()
            logger.error(
                f"💥 Database error during question allocation: {str(db_error)}"
            )
        finally:
            db.close()

    except Exception as e:
        logger.error(f"💥 Error allocating free mode questions: {str(e)}")


async def run_bronze_mode_draw() -> None:
    """
    Process bronze mode draw at the configured draw time.
    Uses generic draw service with registered handlers.
    """
    try:
        logger.info(f"🎯 Starting bronze mode draw at {datetime.now()}")
        db: Session = SessionLocal()

        try:
            # Process yesterday's draw
            yesterday = date.today() - timedelta(days=1)

            # Check if draw already performed
            existing_draw = (
                db.query(TriviaBronzeModeWinners)
                .filter(TriviaBronzeModeWinners.draw_date == yesterday)
                .first()
            )

            if existing_draw:
                logger.info(
                    f"⏭️ Draw for {yesterday} has already been performed, skipping..."
                )
                return

            # Execute draw using generic service
            result = execute_mode_draw(db, "bronze", yesterday)

            if result["status"] == "no_participants":
                logger.info(
                    f"📭 No eligible participants for bronze mode draw on {yesterday}"
                )
                return

            if result["status"] != "success":
                logger.error(
                    f"❌ Draw failed: {result.get('message', 'Unknown error')}"
                )
                return

            # Distribute rewards
            mode_config = get_mode_config(db, "bronze")
            if mode_config:
                winners = result.get("winners", [])
                total_pool = result.get("total_pool", 0.0)
                distribution_result = distribute_rewards_to_winners_bronze_mode(
                    db, winners, yesterday, total_pool
                )

                # Cleanup old leaderboard
                previous_draw_date = yesterday - date.resolution
                cleanup_old_leaderboard_bronze_mode(db, previous_draw_date)

                logger.info("🎉 $5 MODE DRAW COMPLETED SUCCESSFULLY!")
                logger.info(f"🏆 Winners Selected: {len(winners)}")
                logger.info(
                    f"👥 Total Participants: {result.get('total_participants', 0)}"
                )
                logger.info(
                    f"💰 Total Money Awarded: ${distribution_result.get('total_money_awarded', 0):.2f}"
                )

        except Exception as db_error:
            db.rollback()
            logger.error(f"💥 Database error during $5 mode draw: {str(db_error)}")
        finally:
            db.close()

    except Exception as e:
        logger.error(f"💥 Error running $5 mode draw: {str(e)}")


async def allocate_bronze_mode_questions() -> None:
    """
    Allocate bronze mode question for the new day.
    Selects a random question from TriviaQuestionsBronzeMode and adds it to TriviaQuestionsBronzeModeDaily.
    """
    try:
        logger.info(f"🔄 Starting bronze mode question allocation at {datetime.now()}")
        db: Session = SessionLocal()

        try:
            # Get mode config
            mode_config = get_mode_config(db, "bronze")
            if not mode_config:
                logger.warning(
                    "⚠️ Bronze mode config not found, skipping question allocation..."
                )
                return

            target_date = get_active_draw_date()

            # Get date range for the target date
            start_datetime, end_datetime = get_date_range_for_query(target_date)

            # Check if question already allocated for this date
            existing_question = (
                db.query(TriviaQuestionsBronzeModeDaily)
                .filter(
                    TriviaQuestionsBronzeModeDaily.date >= start_datetime,
                    TriviaQuestionsBronzeModeDaily.date <= end_datetime,
                )
                .count()
            )

            if existing_question > 0:
                logger.info(
                    f"⏭️ Question already allocated for {target_date}, skipping..."
                )
                return

            # Get available questions (prefer unused)
            unused_questions = (
                db.query(TriviaQuestionsBronzeMode)
                .filter(TriviaQuestionsBronzeMode.is_used == False)
                .all()
            )

            # If not enough unused questions, get any questions
            import random

            if len(unused_questions) < 1:
                all_questions = db.query(TriviaQuestionsBronzeMode).all()
                if len(all_questions) >= 1:
                    selected_question = random.choice(all_questions)
                else:
                    logger.warning("⚠️ No questions available for bronze mode")
                    return
            else:
                selected_question = random.choice(unused_questions)

            # Allocate question to daily pool
            daily_question = TriviaQuestionsBronzeModeDaily(
                date=start_datetime,
                question_id=selected_question.id,
                question_order=1,  # Always 1 for bronze mode
                is_used=False,
            )
            db.add(daily_question)
            # Mark question as used
            selected_question.is_used = True

            db.commit()
            logger.info(
                f"✅ Successfully allocated question for bronze mode on {target_date}"
            )

        except Exception as db_error:
            db.rollback()
            logger.error(
                f"💥 Database error during bronze mode question allocation: {str(db_error)}"
            )
        finally:
            db.close()

    except Exception as e:
        logger.error(f"💥 Error allocating bronze mode questions: {str(e)}")


def start_scheduler() -> None:
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


def register_mode_handlers() -> None:
    """
    Register mode-specific handlers for the generic draw service.
    """
    # Register free mode handler
    register_mode_handler(
        mode_id="free_mode",
        eligibility_func=get_eligible_participants_free_mode,
        ranking_func=rank_participants_by_completion,
        reward_calc_func=None,  # Uses config value
    )

    # Register $5 mode handler
    register_mode_handler(
        mode_id="bronze",
        eligibility_func=get_eligible_participants_bronze_mode,
        ranking_func=rank_participants_by_submission_time,
        reward_calc_func=calculate_total_pool_bronze_mode,
    )

    # Register silver mode handler
    register_mode_handler(
        mode_id="silver",
        eligibility_func=get_eligible_participants_silver_mode,
        ranking_func=rank_silver_participants,
        reward_calc_func=calculate_total_pool_silver_mode,
    )

    logger.info("Mode handlers registered successfully")


def stop_scheduler() -> None:
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
    logger.info("Scheduler started in standalone mode")
