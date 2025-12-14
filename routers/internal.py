from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, timedelta, datetime
import os
import pytz
from db import get_db
from rewards_logic import perform_draw, reset_monthly_subscriptions, reset_weekly_daily_rewards
import logging
from updated_scheduler import get_detailed_draw_metrics, get_detailed_reset_metrics, get_detailed_monthly_reset_metrics
from models import (
    GlobalChatMessage, User, OneSignalPlayer, TriviaUserDaily,
    TriviaFreeModeWinners, TriviaBronzeModeWinners, TriviaSilverModeWinners, Notification
)
from utils.trivia_mode_service import get_mode_config
from utils.free_mode_rewards import (
    get_eligible_participants_free_mode, rank_participants_by_completion,
    calculate_reward_distribution, distribute_rewards_to_winners, cleanup_old_leaderboard
)
from utils.trivia_mode_service import get_mode_config, get_active_draw_date
from utils.pusher_client import publish_chat_message_sync
from utils.chat_helpers import get_user_chat_profile_data
from utils.onesignal_client import send_push_notification_async
from utils.notification_storage import create_notifications_batch
from config import GLOBAL_CHAT_ENABLED, ONESIGNAL_ENABLED

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
    logging.info(f"📢 send_winner_announcement called for draw_date={draw_date}, winners={len(winners) if winners else 0}")
    
    if not GLOBAL_CHAT_ENABLED:
        logging.warning("Global chat is disabled, skipping winner announcement")
        return
    
    # Get top 6 winners (or fewer if there are less than 6)
    top_winners = sorted(winners, key=lambda x: x.get('position', 999))[:6]
    
    if not top_winners:
        logging.warning(f"No winners to announce (received {len(winners)} winners)")
        return
    
    logging.info(f"Announcing {len(top_winners)} winners for draw_date={draw_date}")
    
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
    
    # Get system user from ADMIN_EMAIL environment variable
    admin_email = os.getenv("ADMIN_EMAIL")
    
    if admin_email:
        # Find user by ADMIN_EMAIL
        system_user = db.query(User).filter(User.email == admin_email).first()
        if system_user:
            system_user_id = system_user.account_id
            logging.info(f"Using admin user from ADMIN_EMAIL (account_id={system_user_id}, email={admin_email}) for winner announcement")
        else:
            logging.error(f"User with ADMIN_EMAIL={admin_email} not found in database. Cannot send winner announcement.")
            return
    else:
        # Fallback: try SYSTEM_USER_ID if ADMIN_EMAIL not set
        system_user_id = int(os.getenv("SYSTEM_USER_ID", "0"))
        if system_user_id > 0:
            system_user = db.query(User).filter(User.account_id == system_user_id).first()
            if system_user:
                logging.info(f"Using system user from SYSTEM_USER_ID (account_id={system_user_id}) for winner announcement")
            else:
                logging.error(f"User with SYSTEM_USER_ID={system_user_id} not found in database. Cannot send winner announcement.")
                return
        else:
            # Final fallback: try to find any admin user
            system_user = db.query(User).filter(User.is_admin == True).first()
            if system_user:
                system_user_id = system_user.account_id
                logging.warning(f"ADMIN_EMAIL not set. Using admin user (account_id={system_user_id}) for winner announcement")
            else:
                logging.error("ADMIN_EMAIL not set and no admin user found. Cannot send winner announcement.")
                return
    
    # Create the message
    system_message = GlobalChatMessage(
        user_id=system_user_id,
        message=message,
        message_type="system",  # Mark as system message
        client_message_id=f"winner_announcement_{draw_date.isoformat()}"  # Unique ID for idempotency
    )
    
    try:
        db.add(system_message)
        db.commit()
        db.refresh(system_message)
        logging.info(f"✅ Winner announcement message saved to database with ID {system_message.id}")
    except Exception as db_error:
        logging.error(f"❌ Failed to save winner announcement to database: {str(db_error)}", exc_info=True)
        db.rollback()
        return
    
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


@router.post("/free-mode-draw")
async def internal_free_mode_draw(
    secret: str = Header(..., alias="X-Secret", description="Secret key for internal calls"),
    db: Session = Depends(get_db)
):
    """
    Internal endpoint for free mode draw triggered by external cron or scheduler.
    
    Determines draw date based on current time and configured draw time:
    - Processes yesterday's draw (same logic as regular draw)
    
    Draw time is configured via DRAW_TIME_HOUR and DRAW_TIME_MINUTE environment variables.
    
    Returns clean response with winner details.
    """
    if secret != os.getenv("INTERNAL_SECRET"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        # Determine which draw date to use (yesterday's draw)
        from utils.trivia_mode_service import get_active_draw_date
        draw_date = get_active_draw_date() - timedelta(days=1)
        
        logging.info(f"🎯 Starting free mode draw for {draw_date} via internal endpoint")
        
        # Check if draw already performed
        existing_draw = db.query(TriviaFreeModeWinners).filter(
            TriviaFreeModeWinners.draw_date == draw_date
        ).first()
        
        if existing_draw:
            logging.info(f"⏭️ Draw for {draw_date} has already been performed")
            return {
                "status": "already_performed",
                "draw_date": draw_date.isoformat(),
                "message": f"Draw for {draw_date} has already been performed"
            }
        
        # Get mode config
        mode_config = get_mode_config(db, 'free_mode')
        if not mode_config:
            logging.error("Free mode config not found")
            raise HTTPException(status_code=404, detail="Free mode config not found")
        
        # Get eligible participants
        participants = get_eligible_participants_free_mode(db, draw_date)
        
        if not participants:
            logging.info(f"No eligible participants for draw on {draw_date}")
            return {
                "status": "no_participants",
                "draw_date": draw_date.isoformat(),
                "message": f"No eligible participants for draw on {draw_date}",
                "total_participants": 0
            }
        
        logging.info(f"Found {len(participants)} eligible participants")
        
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
        distribution_result = distribute_rewards_to_winners(db, winners, mode_config, draw_date)
        
        # Cleanup old leaderboard (previous draw date)
        previous_draw_date = draw_date - timedelta(days=1)
        cleanup_old_leaderboard(db, previous_draw_date)
        
        logging.info(f"✅ Free mode draw completed: {len(winners)} winners, {distribution_result['total_gems_awarded']} gems awarded")
        
        # Get winner details with emails
        winners_data = []
        for winner in winners:
            user = db.query(User).filter(User.account_id == winner['account_id']).first()
            if user:
                winners_data.append({
                    "position": winner.get('position'),
                    "username": winner.get('username'),
                    "email": user.email if user.email else None,
                    "gems_awarded": winner.get('gems_awarded', 0)
                })
        
        # Return clean, simplified response
        return {
            "status": "success",
            "draw_date": draw_date.isoformat(),
            "total_participants": len(ranked_participants),
            "total_winners": len(winners),
            "total_gems_awarded": distribution_result['total_gems_awarded'],
            "winners": winners_data
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"💥 Fatal error in free mode draw: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error in free mode draw: {str(e)}"
        )


@router.post("/mode-draw/{mode_id}")
async def internal_mode_draw(
    mode_id: str,
    secret: str = Header(..., alias="X-Secret", description="Secret key for internal calls"),
    db: Session = Depends(get_db)
):
    """
    Generic internal endpoint for mode draws triggered by external cron or scheduler.
        Supports any registered mode (free_mode, bronze, silver, etc.).
    
    Args:
        mode_id: Mode identifier (e.g., 'free_mode', 'bronze', 'silver')
        
    Returns clean response with winner details.
    """
    if secret != os.getenv("INTERNAL_SECRET"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    try:
        from utils.trivia_mode_service import get_active_draw_date
        from utils.mode_draw_service import execute_mode_draw
        from utils.free_mode_rewards import distribute_rewards_to_winners, cleanup_old_leaderboard
        from utils.bronze_mode_service import (
            distribute_rewards_to_winners_bronze_mode,
            cleanup_old_leaderboard_bronze_mode
        )
        from utils.silver_mode_service import (
            distribute_rewards_to_winners_silver_mode,
            cleanup_old_leaderboard_silver_mode
        )
        from models import TriviaFreeModeWinners, TriviaBronzeModeWinners, TriviaSilverModeWinners
        
        # Determine which draw date to use (yesterday's draw)
        draw_date = get_active_draw_date() - timedelta(days=1)
        
        logging.info(f"🎯 Starting {mode_id} draw for {draw_date} via internal endpoint")
        
        # Check if draw already performed (mode-specific)
        if mode_id == 'free_mode':
            existing_draw = db.query(TriviaFreeModeWinners).filter(
                TriviaFreeModeWinners.draw_date == draw_date
            ).first()
        elif mode_id == 'bronze':
            existing_draw = db.query(TriviaBronzeModeWinners).filter(
                TriviaBronzeModeWinners.draw_date == draw_date
            ).first()
        elif mode_id == 'silver':
            existing_draw = db.query(TriviaSilverModeWinners).filter(
                TriviaSilverModeWinners.draw_date == draw_date
            ).first()
        else:
            existing_draw = None
        
        if existing_draw:
            logging.info(f"⏭️ Draw for {draw_date} has already been performed")
            return {
                "status": "already_performed",
                "draw_date": draw_date.isoformat(),
                "message": f"Draw for {draw_date} has already been performed"
            }
        
        # Execute draw using generic service
        result = execute_mode_draw(db, mode_id, draw_date)
        
        if result.get('status') == 'no_participants':
            logging.info(f"No eligible participants for {mode_id} draw on {draw_date}")
            return {
                "status": "no_participants",
                "draw_date": draw_date.isoformat(),
                "message": f"No eligible participants for draw on {draw_date}",
                "total_participants": 0
            }
        
        if result.get('status') != 'success':
            logging.error(f"Draw failed for {mode_id}: {result.get('message', 'Unknown error')}")
            return {
                "status": result.get('status', 'error'),
                "draw_date": draw_date.isoformat(),
                "message": result.get('message', 'Unknown error')
            }
        
        # Distribute rewards (mode-specific)
        mode_config = get_mode_config(db, mode_id)
        if mode_config:
            winners = result.get('winners', [])
            
            if mode_id == 'free_mode':
                distribution_result = distribute_rewards_to_winners(db, winners, mode_config, draw_date)
                previous_draw_date = draw_date - timedelta(days=1)
                cleanup_old_leaderboard(db, previous_draw_date)
            elif mode_id == 'bronze':
                total_pool = result.get('total_pool', 0.0)
                distribution_result = distribute_rewards_to_winners_bronze_mode(
                    db, winners, draw_date, total_pool
                )
                previous_draw_date = draw_date - timedelta(days=1)
                cleanup_old_leaderboard_bronze_mode(db, previous_draw_date)
            elif mode_id == 'silver':
                total_pool = result.get('total_pool', 0.0)
                distribution_result = distribute_rewards_to_winners_silver_mode(
                    db, winners, draw_date, total_pool
                )
                previous_draw_date = draw_date - timedelta(days=1)
                cleanup_old_leaderboard_silver_mode(db, previous_draw_date)
            else:
                distribution_result = {'total_winners': len(winners)}
            
            # Get winner details with emails
            winners_data = []
            for winner in winners:
                user = db.query(User).filter(User.account_id == winner['account_id']).first()
                if user:
                    winner_data = {
                        "position": winner.get('position'),
                        "username": winner.get('username'),
                        "email": user.email if user.email else None,
                    }
                    # Add reward amount (gems or money)
                    if 'gems_awarded' in winner:
                        winner_data['gems_awarded'] = winner['gems_awarded']
                    if 'reward_amount' in winner:
                        winner_data['money_awarded'] = winner['reward_amount']
                    winners_data.append(winner_data)
            
            logging.info(f"✅ {mode_id} draw completed: {len(winners)} winners")
            
            return {
                "status": "success",
                "draw_date": draw_date.isoformat(),
                "total_participants": result.get('total_participants', 0),
                "total_winners": len(winners),
                "winners": winners_data
            }
        else:
            return {
                "status": "error",
                "draw_date": draw_date.isoformat(),
                "message": f"Mode config not found for {mode_id}"
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"💥 Fatal error in {mode_id} draw: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Error in {mode_id} draw: {str(e)}"
        )


class TriviaReminderRequest(BaseModel):
    """
    Request body for trivia reminder notifications.

    This is a generic app notification (not chat) that reminds users
    to complete today's trivia before the draw.
    """

    heading: str = Field(
        default="Trivia Reminder",
        description="Notification title shown in the push notification",
    )
    message: str = Field(
        default="You still haven't completed today's trivia! Answer now to enter the draw. 🎯",
        description="Notification message body",
    )
    only_incomplete_users: bool = Field(
        default=True,
        description="If true, send only to users who have NOT answered correctly for today's draw date",
    )


@router.post("/trivia-reminder")
async def send_trivia_reminder(
    request: TriviaReminderRequest,
    secret: str = Header(..., alias="X-Secret", description="Secret key for internal calls"),
    db: Session = Depends(get_db),
):
    """
    Internal endpoint to send a push notification reminder for daily trivia.

    - Intended to be called ~1 hour before the draw time by an external cron or scheduler.
    - Sends a OneSignal push notification to:
        * All users with valid OneSignal players, OR
        * Only users who have NOT answered correctly today (default).
    - This is an app-level notification, not tied to chat.
    """
    if secret != os.getenv("INTERNAL_SECRET"):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not ONESIGNAL_ENABLED:
        raise HTTPException(status_code=403, detail="OneSignal is disabled")

    # Check if OneSignal credentials are configured
    from config import ONESIGNAL_APP_ID, ONESIGNAL_REST_API_KEY
    if not ONESIGNAL_APP_ID or not ONESIGNAL_REST_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="OneSignal credentials not configured. Please set ONESIGNAL_APP_ID and ONESIGNAL_REST_API_KEY environment variables."
        )

    try:
        # Import here to avoid circular imports
        from routers.trivia import get_active_draw_date

        # Determine active draw date (the date for which answers are stored)
        active_draw_date = get_active_draw_date()
        logging.info(f"📣 Trivia reminder triggered for draw date: {active_draw_date}")

        # Find users who have answered correctly for the active draw date
        q_correct_users = db.query(TriviaUserDaily.account_id).filter(
            TriviaUserDaily.date == active_draw_date,
            TriviaUserDaily.status == "answered_correct",
        ).distinct()

        correct_user_ids = {row[0] for row in q_correct_users}
        logging.info(
            f"📊 Users who already answered correctly for {active_draw_date}: {len(correct_user_ids)}"
        )

        # Base query: all valid OneSignal players
        players_q = db.query(OneSignalPlayer).filter(OneSignalPlayer.is_valid == True)

        if request.only_incomplete_users and correct_user_ids:
            # Exclude users who have already answered correctly
            players_q = players_q.filter(~OneSignalPlayer.user_id.in_(correct_user_ids))

        players = players_q.all()
        player_ids = [p.player_id for p in players]
        
        # Log which users are being targeted
        unique_user_ids = list(set([p.user_id for p in players]))
        logging.info(f"📋 Targeting {len(unique_user_ids)} unique users with {len(player_ids)} OneSignal players for trivia reminder")
        if unique_user_ids:
            logging.info(f"📋 Sample targeted user_ids: {unique_user_ids[:5]}..." if len(unique_user_ids) > 5 else f"📋 Targeted user_ids: {unique_user_ids}")

        if not player_ids:
            logging.warning(
                f"⚠️ No OneSignal players found for trivia reminder on {active_draw_date} "
                f"(only_incomplete_users={request.only_incomplete_users})"
            )
            return {
                "status": "no_players",
                "sent_to": 0,
                "draw_date": active_draw_date.isoformat(),
                "only_incomplete_users": request.only_incomplete_users,
            }

        # Batch player IDs (OneSignal supports up to ~2000 per request)
        BATCH_SIZE = 2000
        total_sent = 0

        heading = request.heading
        content = request.message
        data = {
            "type": "trivia_reminder",
            "draw_date": active_draw_date.isoformat(),
        }

        # IMPORTANT: This is an app-level reminder, not chat.
        # We DO NOT use the 30-second "active user" suppression here.
        # All targeted users get a normal push notification, even if they're active.

        failed_batches = 0
        for i in range(0, len(player_ids), BATCH_SIZE):
            batch = player_ids[i : i + BATCH_SIZE]
            # Normal system push (not in-app), so is_in_app_notification=False
            ok = await send_push_notification_async(
                player_ids=batch,
                heading=heading,
                content=content,
                data=data,
                is_in_app_notification=False,
            )
            if ok:
                total_sent += len(batch)
            else:
                failed_batches += 1
                logging.warning(f"⚠️ Failed to send trivia reminder to batch of {len(batch)} players")

        # Store notifications in database for all targeted users
        # Get unique user_ids (in case same user has multiple OneSignal players)
        user_ids = list(set([p.user_id for p in players]))
        if user_ids:
            logging.info(f"📝 Storing notifications for {len(user_ids)} unique users. Sample user_ids: {user_ids[:5]}..." if len(user_ids) > 5 else f"📝 Storing notifications for user_ids: {user_ids}")
            notifications_created = create_notifications_batch(
                db=db,
                user_ids=user_ids,
                title=heading,
                body=content,
                notification_type="trivia_reminder",
                data=data
            )
            logging.info(f"📝 Stored {notifications_created} trivia reminder notifications in database for {len(user_ids)} users")
            
            # Verify notifications were actually created
            verification_count = db.query(Notification).filter(
                Notification.type == "trivia_reminder",
                Notification.user_id.in_(user_ids[:5])  # Check first 5
            ).count()
            logging.info(f"🔍 Verification: Found {verification_count} trivia_reminder notifications in DB for sample users (checked {min(5, len(user_ids))} user_ids)")
            
            # Additional verification: Check if any notifications exist at all for this type
            total_trivia_reminders = db.query(func.count(Notification.id)).filter(
                Notification.type == "trivia_reminder"
            ).scalar() or 0
            logging.info(f"🔍 Total trivia_reminder notifications in database: {total_trivia_reminders}")
            
            # Sample a few user_ids to verify they match User.account_id
            if user_ids:
                sample_user_id = user_ids[0]
                user_exists = db.query(User).filter(User.account_id == sample_user_id).first()
                if user_exists:
                    user_notifications = db.query(func.count(Notification.id)).filter(
                        Notification.user_id == sample_user_id,
                        Notification.type == "trivia_reminder"
                    ).scalar() or 0
                    logging.info(f"🔍 Sample user_id {sample_user_id}: User exists, has {user_notifications} trivia_reminder notifications")
                else:
                    logging.warning(f"⚠️ Sample user_id {sample_user_id}: User NOT FOUND in users table!")

        # Only log success if we actually sent to some players
        if total_sent > 0:
            logging.info(
                f"✅ Trivia reminder push sent to {total_sent} players "
                f"(targeted={len(player_ids)}, only_incomplete_users={request.only_incomplete_users})"
            )
        else:
            logging.error(
                f"❌ Trivia reminder push FAILED: sent to 0 players "
                f"(targeted={len(player_ids)}, failed_batches={failed_batches}, only_incomplete_users={request.only_incomplete_users}). "
                f"Check OneSignal credentials and API configuration."
            )

        # Return appropriate status based on whether pushes were sent
        if total_sent == 0:
            return {
                "status": "failed",
                "sent_to": 0,
                "targeted_players": len(player_ids),
                "failed_batches": failed_batches,
                "draw_date": active_draw_date.isoformat(),
                "only_incomplete_users": request.only_incomplete_users,
                "error": "Failed to send push notifications. Check OneSignal credentials and API configuration."
            }
        elif total_sent < len(player_ids):
            return {
                "status": "partial_success",
                "sent_to": total_sent,
                "targeted_players": len(player_ids),
                "failed_batches": failed_batches,
                "draw_date": active_draw_date.isoformat(),
                "only_incomplete_users": request.only_incomplete_users,
                "warning": f"Only {total_sent} out of {len(player_ids)} notifications were sent successfully."
            }
        else:
            return {
                "status": "success",
                "sent_to": total_sent,
                "targeted_players": len(player_ids),
                "draw_date": active_draw_date.isoformat(),
                "only_incomplete_users": request.only_incomplete_users,
            }
    except HTTPException:
        # Pass through HTTP errors unchanged
        raise
    except Exception as e:
        logging.error(f"❌ Error in trivia reminder: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

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
