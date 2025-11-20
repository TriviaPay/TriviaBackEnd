from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import date, timedelta, datetime
import os
from db import get_db
from rewards_logic import perform_draw, reset_monthly_subscriptions, reset_weekly_daily_rewards
import logging
from updated_scheduler import get_detailed_draw_metrics, get_detailed_reset_metrics, get_detailed_monthly_reset_metrics
from models import GlobalChatMessage, User
from utils.pusher_client import publish_chat_message_sync
from config import GLOBAL_CHAT_ENABLED

router = APIRouter(prefix="/internal", tags=["Internal"])


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
        is_from_trivia_live=False,
        client_message_id=f"winner_announcement_{draw_date.isoformat()}"  # Unique ID for idempotency
    )
    
    db.add(system_message)
    db.commit()
    db.refresh(system_message)
    
    # Get system user for display
    system_user = db.query(User).filter(User.account_id == system_user_id).first()
    username = get_display_username(system_user) if system_user else "System"
    
    # Publish to Pusher
    try:
        publish_chat_message_sync(
            "global-chat",
            "new-message",
            {
                "id": system_message.id,
                "user_id": system_user_id,
                "username": username,
                "profile_pic": system_user.profile_pic_url if system_user else None,
                "message": message,
                "created_at": system_message.created_at.isoformat(),
                "is_from_trivia_live": False,
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
            
            # Send winner announcement to global chat if draw was successful
            if result.get('status') == 'success' and result.get('winners'):
                try:
                    send_winner_announcement(db, yesterday, result.get('winners', []))
                    logging.info("✅ Winner announcement sent to global chat")
                except Exception as announcement_error:
                    logging.error(f"❌ Failed to send winner announcement: {str(announcement_error)}", exc_info=True)
                    # Don't fail the draw if announcement fails
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
