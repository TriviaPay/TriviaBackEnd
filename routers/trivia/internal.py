import asyncio
import hashlib

# Legacy perform_draw removed - use mode-specific draws instead
import logging
import os
import secrets
from datetime import date, datetime, timedelta

import pytz
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, union_all
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import GLOBAL_CHAT_ENABLED, ONESIGNAL_ENABLED
from db import get_db, get_db_context

# Note: get_detailed_reset_metrics simplified - legacy tables removed
from models import (  # TriviaUserDaily removed - legacy table
    GlobalChatMessage,
    Notification,
    OneSignalPlayer,
    TriviaBronzeModeWinners,
    TriviaFreeModeWinners,
    TriviaSilverModeWinners,
    TriviaUserBronzeModeDaily,
    TriviaUserFreeModeDaily,
    TriviaUserSilverModeDaily,
    User,
)
from rewards_logic import (
    calculate_prize_pool,
    reset_monthly_subscriptions,
    reset_weekly_daily_rewards,
)
from updated_scheduler import (
    get_detailed_draw_metrics,
    get_detailed_monthly_reset_metrics,
    get_detailed_reset_metrics,
)
from utils.chat_helpers import get_user_chat_profile_data
from utils.free_mode_rewards import (
    calculate_reward_distribution,
    cleanup_old_leaderboard,
    distribute_rewards_to_winners,
    get_eligible_participants_free_mode,
    rank_participants_by_completion,
)
from utils.notification_storage import create_notifications_batch
from utils.onesignal_client import send_push_notification_async
from utils.pusher_client import publish_chat_message_sync
from utils.trivia_mode_service import get_active_draw_date, get_mode_config

router = APIRouter(prefix="/internal", tags=["Internal"])


def _is_authorized(secret: str) -> bool:
    return secrets.compare_digest(secret or "", os.getenv("INTERNAL_SECRET", ""))


def _advisory_lock_key(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (2**63 - 1)


def _try_advisory_lock(db: Session, key: int) -> bool:
    try:
        return bool(
            db.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
        )
    except Exception as exc:
        logging.error(f"Failed to acquire advisory lock: {exc}")
        return False


def _release_advisory_lock(db: Session, key: int) -> None:
    try:
        db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
    except Exception as exc:
        logging.warning(f"Failed to release advisory lock: {exc}")


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
    from routers.trivia.trivia import get_active_draw_date

    # Use the same date logic as users when they answer questions
    # This ensures the draw checks the same date where users stored their answers
    return get_active_draw_date()


def get_display_username(user: User) -> str:
    """Get display username with fallback logic"""
    if user.username and user.username.strip():
        return user.username
    if user.email:
        return user.email.split("@")[0]
    return f"User{user.account_id}"


def send_winner_announcement(db: Session, draw_date: date, winners: list):
    """
    Send winner announcement message to global chat.

    Args:
        db: Database session
        draw_date: The date of the draw
        winners: List of winner dictionaries with 'username' and 'position' keys
    """
    logging.info(
        f"📢 send_winner_announcement called for draw_date={draw_date}, winners={len(winners) if winners else 0}"
    )

    if not GLOBAL_CHAT_ENABLED:
        logging.warning("Global chat is disabled, skipping winner announcement")
        return

    # Get top 6 winners (or fewer if there are less than 6)
    top_winners = sorted(winners, key=lambda x: x.get("position", 999))[:6]

    if not top_winners:
        logging.warning(f"No winners to announce (received {len(winners)} winners)")
        return

    logging.info(f"Announcing {len(top_winners)} winners for draw_date={draw_date}")

    # Build the message
    message_lines = [
        "🎉 Daily Winners Announced! 🎉",
        "Congrats to today's champions on the Trivia Coin leaderboard! 🏆",
    ]

    # Add winners with positions
    medals = ["🥇", "🥈", "🥉"]
    for winner in top_winners:
        position = winner.get("position", 999)
        username = winner.get("username", "Unknown")

        if position == 1:
            message_lines.append(f"{medals[0]} {username}")
        elif position == 2:
            message_lines.append(f"{medals[1]} {username}")
        elif position == 3:
            message_lines.append(f"{medals[2]} {username}")
        else:
            message_lines.append(f"#{position} {username}")

    message_lines.extend(
        [
            "",
            "Your rewards have been added to your accounts. 🙌",
            "Come back tomorrow, answer the daily question, and you could be at the top of the board next! 💰✨",
        ]
    )

    message = "\n".join(message_lines)

    # Get system user from ADMIN_EMAIL environment variable
    admin_email = os.getenv("ADMIN_EMAIL")

    if admin_email:
        # Find user by ADMIN_EMAIL
        system_user = db.query(User).filter(User.email == admin_email).first()
        if system_user:
            system_user_id = system_user.account_id
            logging.info(
                f"Using admin user from ADMIN_EMAIL (account_id={system_user_id}, email={admin_email}) for winner announcement"
            )
        else:
            logging.error(
                f"User with ADMIN_EMAIL={admin_email} not found in database. Cannot send winner announcement."
            )
            return
    else:
        # Fallback: try SYSTEM_USER_ID if ADMIN_EMAIL not set
        system_user_id = int(os.getenv("SYSTEM_USER_ID", "0"))
        if system_user_id > 0:
            system_user = (
                db.query(User).filter(User.account_id == system_user_id).first()
            )
            if system_user:
                logging.info(
                    f"Using system user from SYSTEM_USER_ID (account_id={system_user_id}) for winner announcement"
                )
            else:
                logging.error(
                    f"User with SYSTEM_USER_ID={system_user_id} not found in database. Cannot send winner announcement."
                )
                return
        else:
            # Final fallback: try to find any admin user
            system_user = db.query(User).filter(User.is_admin == True).first()
            if system_user:
                system_user_id = system_user.account_id
                logging.warning(
                    f"ADMIN_EMAIL not set. Using admin user (account_id={system_user_id}) for winner announcement"
                )
            else:
                logging.error(
                    "ADMIN_EMAIL not set and no admin user found. Cannot send winner announcement."
                )
                return

    # Create the message
    system_message = GlobalChatMessage(
        user_id=system_user_id,
        message=message,
        message_type="system",  # Mark as system message
        client_message_id=f"winner_announcement_{draw_date.isoformat()}",  # Unique ID for idempotency
    )

    try:
        existing = (
            db.query(GlobalChatMessage)
            .filter(
                GlobalChatMessage.client_message_id == system_message.client_message_id
            )
            .first()
        )
        if existing:
            logging.info(
                f"Winner announcement already exists for {draw_date}, skipping"
            )
            return
        db.add(system_message)
        db.commit()
        db.refresh(system_message)
        logging.info(
            f"✅ Winner announcement message saved to database with ID {system_message.id}"
        )
    except IntegrityError:
        db.rollback()
        logging.info(f"Winner announcement already exists for {draw_date}, skipping")
        return
    except Exception as db_error:
        logging.error(
            f"❌ Failed to save winner announcement to database: {str(db_error)}",
            exc_info=True,
        )
        db.rollback()
        return

    # Get system user for display
    system_user = db.query(User).filter(User.account_id == system_user_id).first()
    username = "admin"  # Always show as "admin" for system announcements

    # Get system user's profile data (avatar, frame)
    profile_data = (
        get_user_chat_profile_data(system_user, db)
        if system_user
        else {
            "profile_pic_url": None,
            "avatar_url": None,
            "frame_url": None,
            "badge": None,
        }
    )

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
                "badge": profile_data.get("badge"),
                "message": message,
                "created_at": system_message.created_at.isoformat(),
                "message_type": "system",
            },
        )
    except Exception as e:
        logging.error(f"Failed to publish winner announcement to Pusher: {e}")


def _build_trivia_reminder_players_query(
    db: Session, active_draw_date: date, only_incomplete_users: bool
):
    correct_user_ids_subq = None
    if only_incomplete_users:
        free_q = select(TriviaUserFreeModeDaily.account_id).where(
            TriviaUserFreeModeDaily.date == active_draw_date,
            TriviaUserFreeModeDaily.third_question_completed_at.isnot(None),
        )
        bronze_q = select(TriviaUserBronzeModeDaily.account_id).where(
            TriviaUserBronzeModeDaily.date == active_draw_date,
            TriviaUserBronzeModeDaily.is_correct.is_(True),
        )
        silver_q = select(TriviaUserSilverModeDaily.account_id).where(
            TriviaUserSilverModeDaily.date == active_draw_date,
            TriviaUserSilverModeDaily.is_correct.is_(True),
        )
        correct_user_ids_subq = union_all(free_q, bronze_q, silver_q).subquery()

    players_q = (
        db.query(OneSignalPlayer.player_id, OneSignalPlayer.user_id)
        .join(User, User.account_id == OneSignalPlayer.user_id)
        .filter(OneSignalPlayer.is_valid.is_(True), User.notification_on.is_(True))
    )

    if only_incomplete_users and correct_user_ids_subq is not None:
        players_q = players_q.filter(
            ~OneSignalPlayer.user_id.in_(select(correct_user_ids_subq.c.account_id))
        )

    return players_q


def _send_trivia_reminder_job(
    active_draw_date: date, heading: str, content: str, only_incomplete_users: bool
) -> None:
    data = {
        "type": "trivia_reminder",
        "draw_date": active_draw_date.isoformat(),
    }
    BATCH_SIZE = 2000
    failed_batches = 0
    total_sent = 0
    user_ids = set()

    def _send_batch(batch_ids):
        async def _run():
            return await send_push_notification_async(
                player_ids=batch_ids,
                heading=heading,
                content=content,
                data=data,
                is_in_app_notification=False,
            )

        return asyncio.run(_run())

    with get_db_context() as db:
        players_q = _build_trivia_reminder_players_query(
            db, active_draw_date, only_incomplete_users
        )

        batch = []
        for player_id, user_id in players_q.yield_per(1000):
            user_ids.add(user_id)
            batch.append(player_id)
            if len(batch) >= BATCH_SIZE:
                ok = _send_batch(batch)
                if ok:
                    total_sent += len(batch)
                else:
                    failed_batches += 1
                    logging.warning(
                        f"⚠️ Failed to send trivia reminder to batch of {len(batch)} players"
                    )
                batch = []

        if batch:
            ok = _send_batch(batch)
            if ok:
                total_sent += len(batch)
            else:
                failed_batches += 1
                logging.warning(
                    f"⚠️ Failed to send trivia reminder to batch of {len(batch)} players"
                )

        user_ids_list = list(user_ids)
        if user_ids_list:
            CHUNK_SIZE = 1000
            for i in range(0, len(user_ids_list), CHUNK_SIZE):
                chunk = user_ids_list[i : i + CHUNK_SIZE]
                create_notifications_batch(
                    db=db,
                    user_ids=chunk,
                    title=heading,
                    body=content,
                    notification_type="trivia_reminder",
                    data=data,
                )

        if total_sent > 0:
            logging.info(
                f"✅ Trivia reminder push sent to {total_sent} players "
                f"(only_incomplete_users={only_incomplete_users})"
            )
        else:
            logging.error(
                f"❌ Trivia reminder push FAILED: sent to 0 players "
                f"(failed_batches={failed_batches}, only_incomplete_users={only_incomplete_users}). "
                f"Check OneSignal credentials and API configuration."
            )


# Legacy /daily-draw endpoint removed - use mode-specific draws instead:
# - /internal/free-mode-draw
# - /internal/mode-draw/{mode_id}


@router.post("/free-mode-draw")
def internal_free_mode_draw(
    secret: str = Header(
        ..., alias="X-Secret", description="Secret key for internal calls"
    ),
    db: Session = Depends(get_db),
):
    """
    Internal endpoint for free mode draw triggered by external cron or scheduler.

    Determines draw date based on current time and configured draw time:
    - Processes yesterday's draw (same logic as regular draw)

    Draw time is configured via DRAW_TIME_HOUR and DRAW_TIME_MINUTE environment variables.

    Returns clean response with winner details.
    """
    if not _is_authorized(secret):
        raise HTTPException(status_code=401, detail="Unauthorized")

    lock_key = None
    try:
        # Determine which draw date to use (yesterday's draw)
        from utils.trivia_mode_service import get_active_draw_date

        draw_date = get_active_draw_date() - timedelta(days=1)

        lock_key = _advisory_lock_key(f"free_mode:{draw_date.isoformat()}")
        if not _try_advisory_lock(db, lock_key):
            return {
                "status": "already_running",
                "draw_date": draw_date.isoformat(),
                "message": "Draw already running",
            }

        logging.info(
            f"🎯 Starting free mode draw for {draw_date} via internal endpoint"
        )

        # Check if draw already performed
        existing_draw = (
            db.query(TriviaFreeModeWinners)
            .filter(TriviaFreeModeWinners.draw_date == draw_date)
            .first()
        )

        if existing_draw:
            logging.info(f"⏭️ Draw for {draw_date} has already been performed")
            return {
                "status": "already_performed",
                "draw_date": draw_date.isoformat(),
                "message": f"Draw for {draw_date} has already been performed",
            }

        # Get mode config
        mode_config = get_mode_config(db, "free_mode")
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
                "total_participants": 0,
            }

        logging.info(f"Found {len(participants)} eligible participants")

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
            db, winners, mode_config, draw_date
        )

        # Cleanup old leaderboard (previous draw date)
        previous_draw_date = draw_date - timedelta(days=1)
        cleanup_old_leaderboard(db, previous_draw_date)

        logging.info(
            f"✅ Free mode draw completed: {len(winners)} winners, {distribution_result['total_gems_awarded']} gems awarded"
        )

        # Get winner details with emails (bulk fetch)
        winners_data = []
        winner_ids = [winner["account_id"] for winner in winners]
        if winner_ids:
            users = db.query(User).filter(User.account_id.in_(winner_ids)).all()
            users_by_id = {user.account_id: user for user in users}
            for winner in winners:
                user = users_by_id.get(winner["account_id"])
                if user:
                    winners_data.append(
                        {
                            "position": winner.get("position"),
                            "username": winner.get("username"),
                            "email": user.email if user.email else None,
                            "gems_awarded": winner.get("gems_awarded", 0),
                        }
                    )

        # Return clean, simplified response
        return {
            "status": "success",
            "draw_date": draw_date.isoformat(),
            "total_participants": len(ranked_participants),
            "total_winners": len(winners),
            "total_gems_awarded": distribution_result["total_gems_awarded"],
            "winners": winners_data,
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error("💥 Fatal error in free mode draw", exc_info=True)
        raise HTTPException(status_code=500, detail="Error in free mode draw")
    finally:
        if lock_key is not None:
            _release_advisory_lock(db, lock_key)


@router.post("/mode-draw/{mode_id}")
def internal_mode_draw(
    mode_id: str,
    secret: str = Header(
        ..., alias="X-Secret", description="Secret key for internal calls"
    ),
    db: Session = Depends(get_db),
):
    """
    Generic internal endpoint for mode draws triggered by external cron or scheduler.
        Supports any registered mode (free_mode, bronze, silver, etc.).

    Args:
        mode_id: Mode identifier (e.g., 'free_mode', 'bronze', 'silver')

    Returns clean response with winner details.
    """
    if not _is_authorized(secret):
        raise HTTPException(status_code=401, detail="Unauthorized")

    lock_key = None
    try:
        from models import (
            TriviaBronzeModeWinners,
            TriviaFreeModeWinners,
            TriviaSilverModeWinners,
        )
        from utils.bronze_mode_service import (
            cleanup_old_leaderboard_bronze_mode,
            distribute_rewards_to_winners_bronze_mode,
        )
        from utils.free_mode_rewards import (
            cleanup_old_leaderboard,
            distribute_rewards_to_winners,
        )
        from utils.mode_draw_service import execute_mode_draw
        from utils.silver_mode_service import (
            cleanup_old_leaderboard_silver_mode,
            distribute_rewards_to_winners_silver_mode,
        )
        from utils.trivia_mode_service import get_active_draw_date

        # Determine which draw date to use (yesterday's draw)
        draw_date = get_active_draw_date() - timedelta(days=1)

        lock_key = _advisory_lock_key(f"{mode_id}:{draw_date.isoformat()}")
        if not _try_advisory_lock(db, lock_key):
            return {
                "status": "already_running",
                "draw_date": draw_date.isoformat(),
                "message": "Draw already running",
            }

        logging.info(
            f"🎯 Starting {mode_id} draw for {draw_date} via internal endpoint"
        )

        # Check if draw already performed (mode-specific)
        if mode_id == "free_mode":
            existing_draw = (
                db.query(TriviaFreeModeWinners)
                .filter(TriviaFreeModeWinners.draw_date == draw_date)
                .first()
            )
        elif mode_id == "bronze":
            existing_draw = (
                db.query(TriviaBronzeModeWinners)
                .filter(TriviaBronzeModeWinners.draw_date == draw_date)
                .first()
            )
        elif mode_id == "silver":
            existing_draw = (
                db.query(TriviaSilverModeWinners)
                .filter(TriviaSilverModeWinners.draw_date == draw_date)
                .first()
            )
        else:
            existing_draw = None

        if existing_draw:
            logging.info(f"⏭️ Draw for {draw_date} has already been performed")
            return {
                "status": "already_performed",
                "draw_date": draw_date.isoformat(),
                "message": f"Draw for {draw_date} has already been performed",
            }

        # Execute draw using generic service
        result = execute_mode_draw(db, mode_id, draw_date)

        if result.get("status") == "no_participants":
            logging.info(f"No eligible participants for {mode_id} draw on {draw_date}")
            return {
                "status": "no_participants",
                "draw_date": draw_date.isoformat(),
                "message": f"No eligible participants for draw on {draw_date}",
                "total_participants": 0,
            }

        if result.get("status") != "success":
            logging.error(
                f"Draw failed for {mode_id}: {result.get('message', 'Unknown error')}"
            )
            return {
                "status": result.get("status", "error"),
                "draw_date": draw_date.isoformat(),
                "message": result.get("message", "Unknown error"),
            }

        # Distribute rewards (mode-specific)
        mode_config = get_mode_config(db, mode_id)
        if mode_config:
            winners = result.get("winners", [])

            if mode_id == "free_mode":
                distribution_result = distribute_rewards_to_winners(
                    db, winners, mode_config, draw_date
                )
                previous_draw_date = draw_date - timedelta(days=1)
                cleanup_old_leaderboard(db, previous_draw_date)
            elif mode_id == "bronze":
                total_pool = result.get("total_pool", 0.0)
                distribution_result = distribute_rewards_to_winners_bronze_mode(
                    db, winners, draw_date, total_pool
                )
                previous_draw_date = draw_date - timedelta(days=1)
                cleanup_old_leaderboard_bronze_mode(db, previous_draw_date)
            elif mode_id == "silver":
                total_pool = result.get("total_pool", 0.0)
                distribution_result = distribute_rewards_to_winners_silver_mode(
                    db, winners, draw_date, total_pool
                )
                previous_draw_date = draw_date - timedelta(days=1)
                cleanup_old_leaderboard_silver_mode(db, previous_draw_date)
            else:
                distribution_result = {"total_winners": len(winners)}

            # Get winner details with emails (bulk fetch)
            winners_data = []
            winner_ids = [winner["account_id"] for winner in winners]
            users_by_id = {}
            if winner_ids:
                users = db.query(User).filter(User.account_id.in_(winner_ids)).all()
                users_by_id = {user.account_id: user for user in users}
            for winner in winners:
                user = users_by_id.get(winner["account_id"])
                if user:
                    winner_data = {
                        "position": winner.get("position"),
                        "username": winner.get("username"),
                        "email": user.email if user.email else None,
                    }
                    # Add reward amount (gems or money)
                    if "gems_awarded" in winner:
                        winner_data["gems_awarded"] = winner["gems_awarded"]
                    if "reward_amount" in winner:
                        winner_data["money_awarded"] = winner["reward_amount"]
                    winners_data.append(winner_data)

            logging.info(f"✅ {mode_id} draw completed: {len(winners)} winners")

            return {
                "status": "success",
                "draw_date": draw_date.isoformat(),
                "total_participants": result.get("total_participants", 0),
                "total_winners": len(winners),
                "winners": winners_data,
            }
        else:
            return {
                "status": "error",
                "draw_date": draw_date.isoformat(),
                "message": f"Mode config not found for {mode_id}",
            }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"💥 Fatal error in {mode_id} draw", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error in {mode_id} draw")
    finally:
        if lock_key is not None:
            _release_advisory_lock(db, lock_key)


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
def send_trivia_reminder(
    request: TriviaReminderRequest,
    background_tasks: BackgroundTasks,
    secret: str = Header(
        ..., alias="X-Secret", description="Secret key for internal calls"
    ),
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
    if not _is_authorized(secret):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not ONESIGNAL_ENABLED:
        raise HTTPException(status_code=403, detail="OneSignal is disabled")

    # Check if OneSignal credentials are configured
    from config import ONESIGNAL_APP_ID, ONESIGNAL_REST_API_KEY

    if not ONESIGNAL_APP_ID or not ONESIGNAL_REST_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="OneSignal credentials not configured. Please set ONESIGNAL_APP_ID and ONESIGNAL_REST_API_KEY environment variables.",
        )

    try:
        # Import here to avoid circular imports
        from routers.trivia.trivia import get_active_draw_date

        # Determine active draw date (the date for which answers are stored)
        active_draw_date = get_active_draw_date()
        logging.info(f"📣 Trivia reminder triggered for draw date: {active_draw_date}")

        players_q = _build_trivia_reminder_players_query(
            db, active_draw_date, request.only_incomplete_users
        )
        players_subq = players_q.subquery()
        total_targeted = db.query(func.count()).select_from(players_subq).scalar() or 0
        total_users = (
            db.query(func.count(func.distinct(players_subq.c.user_id))).scalar() or 0
        )

        if total_targeted == 0:
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

        background_tasks.add_task(
            _send_trivia_reminder_job,
            active_draw_date,
            request.heading,
            request.message,
            request.only_incomplete_users,
        )

        return {
            "status": "queued",
            "targeted_players": total_targeted,
            "targeted_users": total_users,
            "draw_date": active_draw_date.isoformat(),
            "only_incomplete_users": request.only_incomplete_users,
        }
    except HTTPException:
        # Pass through HTTP errors unchanged
        raise
    except Exception as e:
        logging.error("❌ Error in trivia reminder", exc_info=True)
        raise HTTPException(status_code=500, detail="Error sending trivia reminder")


# Legacy /question-reset endpoint removed - TriviaQuestionsDaily and Trivia tables deleted
# Use mode-specific question management instead


@router.post("/monthly-reset")
def internal_monthly_reset(
    secret: str = Header(
        ..., alias="X-Secret", description="Secret key for internal calls"
    ),
    db: Session = Depends(get_db),
):
    """Internal endpoint for monthly subscription reset triggered by external cron"""
    if not _is_authorized(secret):
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
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logging.error("Error in monthly reset", exc_info=True)
        raise HTTPException(status_code=500, detail="Monthly reset failed")


@router.post("/weekly-rewards-reset")
def internal_weekly_rewards_reset(
    secret: str = Header(
        ..., alias="X-Secret", description="Secret key for internal calls"
    ),
    db: Session = Depends(get_db),
):
    """Internal endpoint for weekly daily rewards reset triggered by external cron"""
    if not _is_authorized(secret):
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
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logging.error("Error in weekly rewards reset", exc_info=True)
        raise HTTPException(status_code=500, detail="Weekly rewards reset failed")


@router.post("/daily-revenue-update")
def internal_daily_revenue_update(
    secret: str = Header(
        ..., alias="X-Secret", description="Secret key for internal calls"
    ),
    db: Session = Depends(get_db),
):
    """Internal endpoint for daily company revenue updates triggered by external cron"""
    if not _is_authorized(secret):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        draw_date = get_today_in_app_timezone()
        calculate_prize_pool(db, draw_date, commit_revenue=True)

        logging.info(
            f"Daily company revenue update completed via external cron for {draw_date}"
        )
        return {
            "status": "success",
            "message": "Company revenue updated",
            "triggered_by": "external_cron",
            "draw_date": draw_date.isoformat(),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logging.error("Error in daily revenue update", exc_info=True)
        raise HTTPException(status_code=500, detail="Daily revenue update failed")


@router.get("/health")
def internal_health():
    """Health check for external cron services"""
    return {
        "status": "healthy",
        "service": "triviapay-internal",
        "timestamp": datetime.utcnow().isoformat(),
    }
