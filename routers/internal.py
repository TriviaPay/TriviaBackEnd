from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import date, timedelta, datetime
import os
import pytz
from db import get_db
from rewards_logic import perform_draw, reset_monthly_subscriptions, reset_weekly_daily_rewards
import logging
from updated_scheduler import get_detailed_draw_metrics, get_detailed_reset_metrics, get_detailed_monthly_reset_metrics
from models import GlobalChatMessage, User
from utils.pusher_client import publish_chat_message_sync
from utils.chat_helpers import get_user_chat_profile_data
from config import GLOBAL_CHAT_ENABLED

router = APIRouter(prefix="/internal", tags=["Internal"])


def get_today_in_app_timezone() -> date:
    """Get today's date in the app's timezone (EST/US Eastern)."""
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    return now.date()


def get_draw_date_for_today() -> date:
    """
    Determine which draw date to use for the draw.
    
    The draw should check for the "next draw date" - the date that users are currently
    answering questions for. This matches the active_draw_date where users store their answers.
    
    The "next draw" represents the draw happening at today's draw time, but users answer
    questions for tomorrow's date (the next draw date), so we check for tomorrow's date.
    """
    # Import get_active_draw_date from trivia router to use the exact same logic
    from routers.trivia import get_active_draw_date
    # Use the same date logic as users when they answer questions
    # This ensures the draw checks the same date where users stored their answers
    return get_active_draw_date()


def get_display_username(user: User) -> str:
    """Get display username with fallback logic"""
    if user.username and user.username.strip():
        return user.username
    if user.email:
        return user.email.split('@')[0]
    return f"User{user.account_id}"


def send_winner_announcement(db: Session, draw_date: date, winners: list):
    """
    Send winner announcement message to global chat.
    
    Args:
        db: Database session
        draw_date: The date of the draw
        winners: List of winner dictionaries with 'username' and 'position' keys
    """
    if not GLOBAL_CHAT_ENABLED:
        logging.warning("Global chat is disabled, skipping winner announcement")
        return
    
    # Get top 6 winners (or fewer if there are less than 6)
    top_winners = sorted(winners, key=lambda x: x.get('position', 999))[:6]
    
    if not top_winners:
        logging.warning("No winners to announce")
        return
    
    # Build the message
    message_lines = [
        "🎉 Daily Winners Announced! 🎉",
        "Congrats to today's champions on the Trivia Coin leaderboard! 🏆"
    ]
    
    # Add winners with positions
    medals = ["🥇", "🥈", "🥉"]
    for winner in top_winners:
        position = winner.get('position', 999)
        username = winner.get('username', 'Unknown')
        
        if position == 1:
            message_lines.append(f"{medals[0]} {username}")
        elif position == 2:
            message_lines.append(f"{medals[1]} {username}")
        elif position == 3:
            message_lines.append(f"{medals[2]} {username}")
        else:
            message_lines.append(f"#{position} {username}")
    
    message_lines.extend([
        "",
        "Your rewards have been added to your accounts. 🙌",
        "Come back tomorrow, answer the daily question, and you could be at the top of the board next! 💰✨"
    ])
    
    message = "\n".join(message_lines)
    
    # Get or create a system user (you might want to use a specific system account_id)
    # For now, we'll use a special system user ID (you may want to configure this)
    system_user_id = int(os.getenv("SYSTEM_USER_ID", "0"))  # Default to 0, but should be configured
    
    if system_user_id == 0:
        # Try to find a system/admin user
        system_user = db.query(User).filter(User.is_admin == True).first()
        if system_user:
            system_user_id = system_user.account_id
        else:
            logging.error("No system user found for sending winner announcement")
            return
    
    # Create the message
    system_message = GlobalChatMessage(
        user_id=system_user_id,
        message=message,
        message_type="system",  # Mark as system message
        client_message_id=f"winner_announcement_{draw_date.isoformat()}"  # Unique ID for idempotency
    )
    
    db.add(system_message)
    db.commit()
    db.refresh(system_message)
    
    # Get system user for display
    system_user = db.query(User).filter(User.account_id == system_user_id).first()
    username = "admin"  # Always show as "admin" for system announcements
    
    # Get system user's profile data (avatar, frame)
    profile_data = get_user_chat_profile_data(system_user, db) if system_user else {
        "profile_pic_url": None,
        "avatar_url": None,
        "frame_url": None
    }
    
    # Publish to Pusher
    try:
        publish_chat_message_sync(
            "global-chat",
            "new-message",
            {
                "id": system_message.id,
                "user_id": system_user_id,
                "username": username,
                "profile_pic": profile_data["profile_pic_url"],
                "avatar_url": profile_data["avatar_url"],
                "frame_url": profile_data["frame_url"],
                "badge": profile_data["badge"],
                "message": message,
                "created_at": system_message.created_at.isoformat(),
                "message_type": "system"
            }
        )
    except Exception as e:
        logging.error(f"Failed to publish winner announcement to Pusher: {e}")

@router.post("/daily-draw")
async def internal_daily_draw(
    secret: str = Header(..., alias="X-Secret", description="Secret key for internal calls"),
    db: Session = Depends(get_db)
):
    """
    Internal endpoint for daily draw triggered by external cron.
    
    Determines draw date based on current time and configured draw time:
    - If called after draw time - 12 AM: triggers draw for today (the draw that happened at draw time)
    - If called between 12 AM - draw time: triggers draw for today (today's draw, which will happen at draw time)
    
    Draw time is configured via DRAW_TIME_HOUR and DRAW_TIME_MINUTE environment variables.
    
    Returns clean response with winner emails.
    """
    if secret != os.getenv("INTERNAL_SECRET"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        # Determine which draw date to use based on current time
        draw_date = get_draw_date_for_today()
        
        logging.info(f"🎯 Starting daily draw for {draw_date} via external cron")
        
        # Perform the draw
        logging.info("🎲 Performing draw...")
        result = perform_draw(db, draw_date)
        logging.info(f"✅ Draw completed: {result.get('status', 'unknown')} - {result.get('total_participants', 0)} participants, {result.get('total_winners', 0)} winners")
        
        # Handle different draw result statuses
        if result.get('status') == 'already_performed':
            return {
                "status": "already_performed",
                "draw_date": draw_date.isoformat(),
                "message": f"Draw for {draw_date} has already been performed"
            }
        
        if result.get('status') == 'no_participants':
            return {
                "status": "no_participants",
                "draw_date": draw_date.isoformat(),
                "message": f"No eligible participants for draw on {draw_date}",
                "total_participants": 0
            }
        
        if result.get('status') != 'success':
            return {
                "status": result.get('status', 'error'),
                "draw_date": draw_date.isoformat(),
                "message": result.get('message', 'Unknown error')
            }
        
        # Get winner details with emails
        winners_data = []
        for winner in result.get('winners', []):
            user = db.query(User).filter(User.account_id == winner['account_id']).first()
            if user:
                winners_data.append({
                    "position": winner.get('position'),
                    "username": winner.get('username'),
                    "email": user.email if user.email else None,
                    "prize_amount": winner.get('prize_amount', 0)
                })
        
        # Send winner announcement to global chat if draw was successful
        if winners_data:
            try:
                send_winner_announcement(db, draw_date, result.get('winners', []))
                logging.info("✅ Winner announcement sent to global chat")
            except Exception as announcement_error:
                logging.error(f"❌ Failed to send winner announcement: {str(announcement_error)}", exc_info=True)
                # Don't fail the draw if announcement fails
        
        # Return clean, simplified response
        return {
            "status": "success",
            "draw_date": draw_date.isoformat(),
            "total_participants": result.get('total_participants', 0),
            "total_winners": result.get('total_winners', 0),
            "prize_pool": result.get('prize_pool', 0),
            "winners": winners_data
        }
    except Exception as e:
        logging.error(f"💥 Fatal error in daily draw: {str(e)}", exc_info=True)
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
    # Log immediately when endpoint is called
    logging.info("=" * 80)
    logging.info("🚀 QUESTION RESET ENDPOINT CALLED")
    logging.info(f"⏰ Timestamp: {datetime.now()}")
    logging.info("=" * 80)
    
    if secret != os.getenv("INTERNAL_SECRET"):
        logging.error("❌ UNAUTHORIZED: Invalid secret key")
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    logging.info("✅ Secret key validated - proceeding with question reset")
    
    try:
        # Log timezone info for debugging
        from cleanup_unused_questions import get_today_in_app_timezone, get_date_range_for_query
        from models import TriviaQuestionsDaily
        
        today = get_today_in_app_timezone()
        start_datetime, end_datetime = get_date_range_for_query(today)
        
        logging.info(f"🔄 Question reset triggered at {datetime.now()}")
        logging.info(f"📅 Today in app timezone: {today}")
        logging.info(f"📅 Date range: {start_datetime} to {end_datetime}")
        
        # Get initial pool count
        initial_pool_count = db.query(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= start_datetime,
            TriviaQuestionsDaily.date <= end_datetime
        ).count()
        logging.info(f"📊 Initial pool count: {initial_pool_count} questions")
        
        # Get detailed metrics before reset
        logging.info("📊 Collecting detailed reset metrics...")
        metrics = get_detailed_reset_metrics(db)
        
        # Import here to avoid circular imports
        from cleanup_unused_questions import cleanup_unused_questions
        from rewards_logic import reset_daily_eligibility_flags
        
        # Clean up unused questions
        logging.info("🧹 Cleaning up unused questions...")
        cleanup_unused_questions()
        
        # After the draw, today's pool is expected to be empty (all questions were used)
        # We need to verify the NEXT draw's pool has questions instead
        next_draw_date = today + timedelta(days=1)
        next_start_datetime, next_end_datetime = get_date_range_for_query(next_draw_date)
        
        # Check next draw's pool (this is what matters - cleanup populates this)
        next_draw_pool_count = db.query(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= next_start_datetime,
            TriviaQuestionsDaily.date <= next_end_datetime
        ).count()
        
        # Get the actual questions for debugging
        next_pool_questions = db.query(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= next_start_datetime,
            TriviaQuestionsDaily.date <= next_end_datetime
        ).all()
        
        # Also check today's pool for informational purposes (expected to be empty after draw)
        today_pool_count = db.query(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= start_datetime,
            TriviaQuestionsDaily.date <= end_datetime
        ).count()
        
        logging.info(f"✅ Next draw's pool after cleanup: {next_draw_pool_count} questions")
        logging.info(f"📅 Today's pool after cleanup: {today_pool_count} questions (expected to be empty after draw)")
        
        if next_pool_questions:
            logging.info(f"Next draw pool questions: {[(q.question_number, q.question_order, q.date) for q in next_pool_questions]}")
        
        # Today's pool being empty is expected after the draw - just log it
        if today_pool_count == 0:
            logging.info("ℹ️  Today's pool is empty (expected after draw completion)")
        elif today_pool_count > 0:
            logging.info(f"ℹ️  Today's pool has {today_pool_count} questions (some may remain unused)")
        
        # Verify next draw's pool has questions (this is what matters)
        if next_draw_pool_count == 0:
            error_msg = f"CRITICAL: Next draw's pool is empty after cleanup! Next draw date: {next_draw_date}"
            logging.error(f"❌ {error_msg}")
            logging.error(f"Initial pool count: {initial_pool_count}, Next draw pool count: {next_draw_pool_count}")
            logging.error(f"Next draw date range: {next_start_datetime} to {next_end_datetime}")
            raise HTTPException(
                status_code=500,
                detail=f"{error_msg} Check cleanup_unused_questions() function. Next draw date: {next_draw_date}"
            )
        elif next_draw_pool_count < 4:
            logging.warning(f"⚠️  WARNING: Next draw's pool has only {next_draw_pool_count} questions (should have 4)")
        
        # Reset eligibility flags
        logging.info("🔄 Resetting eligibility flags...")
        reset_daily_eligibility_flags(db)
        
        logging.info("=" * 80)
        logging.info("✅ Question reset completed via external cron")
        logging.info(f"📊 Final Results:")
        logging.info(f"   - Initial pool count: {initial_pool_count}")
        logging.info(f"   - Today's pool count: {today_pool_count} (expected to be empty after draw)")
        logging.info(f"   - Next draw's pool count: {next_draw_pool_count}")
        logging.info(f"   - Today: {today}")
        logging.info(f"   - Next draw date: {next_draw_date}")
        logging.info("=" * 80)
        
        return {
            "status": "success",
            "message": "Questions reset and eligibility flags cleared",
            "triggered_by": "external_cron",
            "detailed_metrics": metrics,
            "today_pool_count": today_pool_count,
            "next_draw_pool_count": next_draw_pool_count,
            "initial_pool_count": initial_pool_count,
            "today": today.isoformat(),
            "next_draw_date": next_draw_date.isoformat(),
            "timestamp": datetime.now().isoformat()
        }
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logging.error("=" * 80)
        logging.error(f"❌ ERROR in question reset: {str(e)}")
        logging.error("=" * 80)
        import traceback
        logging.error(traceback.format_exc())
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
