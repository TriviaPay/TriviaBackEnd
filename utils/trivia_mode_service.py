"""
Generic service for trivia mode operations.
"""

import logging
import os
import random
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import pytz
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from models import (
    TriviaModeConfig,
    TriviaQuestionsFreeMode,
    TriviaQuestionsFreeModeDaily,
    TriviaUserFreeModeDaily,
    User,
)

logger = logging.getLogger(__name__)


def _select_random_rows(base_query, count: int, order_col):
    """
    Select a random subset using indexed ordering and offsets.
    Avoids ORDER BY random() full scans.
    """
    total = base_query.count()
    if total <= 0:
        return []
    if total <= count:
        return base_query.order_by(order_col).all()

    offsets = random.sample(range(total), count)
    results = []
    seen_ids = set()
    for offset in offsets:
        row = base_query.order_by(order_col).offset(offset).limit(1).first()
        if row and row.id not in seen_ids:
            results.append(row)
            seen_ids.add(row.id)
    while len(results) < count:
        offset = random.randrange(total)
        row = base_query.order_by(order_col).offset(offset).limit(1).first()
        if row and row.id not in seen_ids:
            results.append(row)
            seen_ids.add(row.id)
    return results


def get_mode_config(db: Session, mode_id: str) -> Optional[TriviaModeConfig]:
    """
    Get mode configuration by mode_id.

    Args:
        db: Database session
        mode_id: The mode identifier

    Returns:
        TriviaModeConfig object or None if not found
    """
    return (
        db.query(TriviaModeConfig).filter(TriviaModeConfig.mode_id == mode_id).first()
    )


def get_today_in_app_timezone() -> date:
    """Get today's date in the app's timezone (EST/US Eastern)."""
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    return now.date()


def get_draw_time_settings() -> Dict[str, Any]:
    return {
        "hour": int(os.getenv("DRAW_TIME_HOUR", "18")),
        "minute": int(os.getenv("DRAW_TIME_MINUTE", "0")),
        "timezone": os.getenv("DRAW_TIMEZONE", "US/Eastern"),
        "reset_delay_minutes": int(os.getenv("DRAW_RESET_DELAY_MINUTES", "30")),
    }


def get_reset_window_status(current_time: datetime = None) -> Dict[str, Any]:
    """
    Return whether we are in the post-draw reset window and how many minutes remain.
    """
    import math

    settings = get_draw_time_settings()
    tz = pytz.timezone(settings["timezone"])
    now = current_time.astimezone(tz) if current_time else datetime.now(tz)

    draw_time = now.replace(
        hour=settings["hour"],
        minute=settings["minute"],
        second=0,
        microsecond=0,
    )
    reset_time = draw_time + timedelta(minutes=settings["reset_delay_minutes"])
    in_window = draw_time <= now < reset_time
    minutes_left = 0
    if in_window:
        remaining_seconds = max((reset_time - now).total_seconds(), 0)
        minutes_left = int(math.ceil(remaining_seconds / 60.0))

    return {
        "in_reset_window": in_window,
        "minutes_left": minutes_left,
        "reset_time": reset_time,
        "draw_time": draw_time,
        "reset_delay_minutes": settings["reset_delay_minutes"],
    }


def get_active_draw_date() -> date:
    """
    Get the draw date for which users should see questions.

    Logic:
    - Before draw time: return yesterday's date
    - After draw time: return today's date
    - After 12 AM (midnight): return yesterday again until draw time

    This ensures that:
    - Before draw time, users see questions for the current draw period
      (yesterday's date)
    - After draw time, users see questions for the next draw period (today's date)
    """
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    today = now.date()

    # Get draw time configuration
    draw_time_hour = int(os.getenv("DRAW_TIME_HOUR", "18"))  # Default 6 PM
    draw_time_minute = int(os.getenv("DRAW_TIME_MINUTE", "0"))

    # Create draw time for today
    draw_time = now.replace(
        hour=draw_time_hour,
        minute=draw_time_minute,
        second=0,
        microsecond=0,
    )

    # If current time is before draw time, return yesterday
    # If current time is after draw time, return today
    if now < draw_time:
        # Before draw time: return yesterday
        return today - timedelta(days=1)
    else:
        # After draw time: return today
        return today


def get_date_range_for_query(target_date: date):
    """
    Get UTC datetime range for a given date in app timezone.
    Returns start and end UTC datetimes for the date.
    """
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)

    # Start of day in app timezone
    start_local = tz.localize(datetime.combine(target_date, datetime.min.time()))
    start_utc = start_local.astimezone(pytz.UTC)

    # End of day in app timezone
    end_local = tz.localize(datetime.combine(target_date, datetime.max.time()))
    end_utc = end_local.astimezone(pytz.UTC)

    return start_utc, end_utc


def _ensure_mode_config(db: Session, mode_id: str) -> Optional[TriviaModeConfig]:
    """
    Ensure a mode config exists; create a default for free_mode if missing.
    """
    mode_config = get_mode_config(db, mode_id)
    if not mode_config and mode_id == "free_mode":
        try:
            mode_config = TriviaModeConfig(
                mode_id="free_mode",
                mode_name="Free Mode",
                questions_count=3,
                reward_distribution=(
                    '{"winner_count_formula": "tiered", '
                    '"tiered_config": {"default": 1}, '
                    '"gem_shares": [100]}'
                ),
                amount=0.0,
                leaderboard_types='["daily"]',
                ad_config="{}",
                survey_config="{}",
            )
            db.add(mode_config)
            db.commit()
            logger.info("Created default Free Mode config")
        except Exception as exc:
            db.rollback()
            logger.error(f"Failed to create Free Mode config: {exc}", exc_info=True)
            return None
    return mode_config


def get_daily_questions_for_mode(
    db: Session, mode_id: str, user: User, target_date: Optional[date] = None
) -> List[Dict[str, Any]]:
    """
    Get daily questions for a specific mode and user.

    Args:
        db: Database session
        mode_id: Mode identifier
        user: User object
        target_date: Optional target date (defaults to active draw date)

    Returns:
        List of question dictionaries with unlock status
    """
    if target_date is None:
        target_date = get_active_draw_date()

    mode_config = _ensure_mode_config(db, mode_id)
    if not mode_config:
        logger.error(
            f"Mode config missing for mode_id={mode_id}, cannot load questions"
        )
        return []

    # Get questions based on mode
    if mode_id == "free_mode":
        return get_free_mode_questions(db, user, target_date)

    # Add more modes here
    return []


def get_free_mode_questions(
    db: Session,
    user: User,
    target_date: date,
) -> List[Dict[str, Any]]:
    """
    Get free mode questions for a user.
    Automatically allocates questions if they don't exist for the target date.

    Args:
        db: Database session
        user: User object
        target_date: Target date

    Returns:
        List of question dictionaries
    """
    start_datetime, end_datetime = get_date_range_for_query(target_date)

    # Get daily pool with eager loading of questions
    daily_pool = (
        db.query(TriviaQuestionsFreeModeDaily)
        .options(joinedload(TriviaQuestionsFreeModeDaily.question))
        .filter(
            TriviaQuestionsFreeModeDaily.date >= start_datetime,
            TriviaQuestionsFreeModeDaily.date <= end_datetime,
        )
        .order_by(TriviaQuestionsFreeModeDaily.question_order)
        .all()
    )

    # Auto-allocate questions if pool is empty
    if not daily_pool:
        logger.info(
            f"No questions found for date {target_date}, attempting auto-allocation..."
        )
        try:
            # Get mode config
            mode_config = get_mode_config(db, "free_mode")
            if not mode_config:
                logger.error(
                    "Free mode config not found, cannot auto-allocate questions"
                )
                # Try to create default config
                try:
                    mode_config = TriviaModeConfig(
                        mode_id="free_mode",
                        mode_name="Free Mode",
                        questions_count=3,
                        reward_distribution=(
                            '{"winner_count_formula": "tiered", '
                            '"tiered_config": {"default": 1}, '
                            '"gem_shares": [100]}'
                        ),
                        amount=0.0,
                        leaderboard_types='["daily"]',
                        ad_config="{}",
                        survey_config="{}",
                    )
                    db.add(mode_config)
                    db.commit()
                    logger.info("Created default free mode config")
                except Exception as create_error:
                    logger.error(f"Failed to create mode config: {str(create_error)}")
                    return []

            questions_count = mode_config.questions_count
            logger.info(f"Mode config found, questions_count: {questions_count}")

            # Get available questions (prefer unused)
            unused_count = (
                db.query(func.count(TriviaQuestionsFreeMode.id))
                .filter(TriviaQuestionsFreeMode.is_used.is_(False))
                .scalar()
                or 0
            )

            logger.info(f"Found {unused_count} unused questions")

            # If not enough unused questions, fall back to any questions
            if unused_count < questions_count:
                all_count = (
                    db.query(func.count(TriviaQuestionsFreeMode.id)).scalar() or 0
                )
                logger.info(
                    f"Not enough unused questions, using all {all_count} questions"
                )
                if all_count == 0:
                    available_questions = []
                else:
                    available_questions = _select_random_rows(
                        db.query(TriviaQuestionsFreeMode),
                        questions_count,
                        TriviaQuestionsFreeMode.id,
                    )
            else:
                available_questions = _select_random_rows(
                    db.query(TriviaQuestionsFreeMode).filter(
                        TriviaQuestionsFreeMode.is_used.is_(False)
                    ),
                    questions_count,
                    TriviaQuestionsFreeMode.id,
                )

            if len(available_questions) == 0:
                logger.error("No questions available to allocate")
                return []

            logger.info(f"Selected {len(available_questions)} questions to allocate")

            # Allocate questions to daily pool
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

            try:
                db.commit()
                logger.info(
                    f"Successfully allocated {len(available_questions)} questions"
                )
            except IntegrityError:
                # Another process likely allocated concurrently; fall back to re-query
                db.rollback()
                logger.warning("Auto-allocation hit a race; reloading daily pool")

            # Re-query the daily pool with eager loading
            daily_pool = (
                db.query(TriviaQuestionsFreeModeDaily)
                .options(joinedload(TriviaQuestionsFreeModeDaily.question))
                .filter(
                    TriviaQuestionsFreeModeDaily.date >= start_datetime,
                    TriviaQuestionsFreeModeDaily.date <= end_datetime,
                )
                .order_by(TriviaQuestionsFreeModeDaily.question_order)
                .all()
            )

            logger.info(f"Re-queried daily pool, found {len(daily_pool)} questions")
        except Exception as e:
            db.rollback()
            logger.error(f"Error during auto-allocation: {str(e)}", exc_info=True)
            # Don't return empty, try to continue with whatever we have

    # Get user's attempts
    user_attempts = {
        (ud.date, ud.question_order): ud
        for ud in db.query(TriviaUserFreeModeDaily)
        .filter(
            TriviaUserFreeModeDaily.account_id == user.account_id,
            TriviaUserFreeModeDaily.date == target_date,
        )
        .all()
    }

    questions = []
    for dq in daily_pool:
        # Eagerly load the question relationship
        question = dq.question
        if question is None:
            logger.warning(
                f"Question {dq.question_id} not found for daily pool entry {dq.id}"
            )
            continue

        user_attempt = user_attempts.get((target_date, dq.question_order))

        question_data = {
            "question_id": dq.question_id,
            "question_order": dq.question_order,
            "question": question.question,
            "option_a": question.option_a,
            "option_b": question.option_b,
            "option_c": question.option_c,
            "option_d": question.option_d,
            "correct_answer": get_correct_answer_letter(question),
            "hint": question.hint,
            "fill_in_answer": (
                user_attempt.user_answer
                if user_attempt and user_attempt.user_answer
                else None
            ),
            "explanation": question.explanation,
            "category": question.category,
            "difficulty_level": question.difficulty_level,
            "picture_url": question.picture_url,
            "status": user_attempt.status if user_attempt else "locked",
            "is_correct": user_attempt.is_correct if user_attempt else None,
            "answered_at": (
                user_attempt.answered_at.isoformat()
                if user_attempt and user_attempt.answered_at
                else None
            ),
        }
        questions.append(question_data)

    logger.info(
        f"Returning {len(questions)} questions for free mode, "
        f"target_date: {target_date}"
    )
    return questions


def submit_answer_for_mode(
    db: Session,
    mode_id: str,
    user: User,
    question_id: int,
    answer: str,
    target_date: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Submit an answer for a question in a specific mode.

    Args:
        db: Database session
        mode_id: Mode identifier
        user: User object
        question_id: Question ID
        answer: User's answer
        target_date: Optional target date

    Returns:
        Dictionary with result status
    """
    if target_date is None:
        target_date = get_active_draw_date()

    if mode_id == "free_mode":
        return submit_free_mode_answer(db, user, question_id, answer, target_date)

    return {"status": "error", "message": f"Unknown mode: {mode_id}"}


def submit_free_mode_answer(
    db: Session, user: User, question_id: int, answer: str, target_date: date
) -> Dict[str, Any]:
    """
    Submit answer for free mode question.

    Args:
        db: Database session
        user: User object
        question_id: Question ID
        answer: User's answer
        target_date: Target date

    Returns:
        Dictionary with result
    """
    # Get the question
    question = (
        db.query(TriviaQuestionsFreeMode)
        .filter(TriviaQuestionsFreeMode.id == question_id)
        .first()
    )

    if not question:
        return {
            "status": "error",
            "message": "Question not found",
            "is_correct": False,
        }

    # Find or create user attempt
    start_datetime, end_datetime = get_date_range_for_query(target_date)
    daily_q = (
        db.query(TriviaQuestionsFreeModeDaily)
        .filter(
            TriviaQuestionsFreeModeDaily.date >= start_datetime,
            TriviaQuestionsFreeModeDaily.date <= end_datetime,
            TriviaQuestionsFreeModeDaily.question_id == question_id,
        )
        .first()
    )

    if not daily_q:
        return {
            "status": "error",
            "message": "Question not available for today",
            "is_correct": False,
        }

    user_attempt = (
        db.query(TriviaUserFreeModeDaily)
        .filter(
            TriviaUserFreeModeDaily.account_id == user.account_id,
            TriviaUserFreeModeDaily.date == target_date,
            TriviaUserFreeModeDaily.question_order == daily_q.question_order,
        )
        .first()
    )

    if not user_attempt:
        # Create new attempt
        user_attempt = TriviaUserFreeModeDaily(
            account_id=user.account_id,
            date=target_date,
            question_order=daily_q.question_order,
            question_id=question_id,
            status="viewed",
        )
        db.add(user_attempt)

    # Check if already answered
    if user_attempt.status in ["answered_correct", "answered_wrong"]:
        return {
            "status": "error",
            "message": f"Question already answered ({user_attempt.status})",
            "is_correct": user_attempt.is_correct,
        }

    # Check answer (compare normalized letters)
    correct_letter = get_correct_answer_letter(question)
    submitted_letter = (answer or "").strip().lower()
    is_correct = submitted_letter == correct_letter

    # Update attempt
    user_attempt.user_answer = answer
    user_attempt.is_correct = is_correct
    user_attempt.answered_at = datetime.utcnow()
    user_attempt.status = "answered_correct" if is_correct else "answered_wrong"

    # If this is the 3rd question and it's correct, set completion time
    if is_correct and daily_q.question_order == 3:
        user_attempt.third_question_completed_at = datetime.utcnow()

    db.commit()

    # Track answer and update user level
    from utils.user_level_service import track_answer_and_update_level

    level_info = track_answer_and_update_level(user, db)

    return {
        "status": "success",
        "is_correct": is_correct,
        "message": "Correct!" if is_correct else "Incorrect. Try again tomorrow!",
        "level_info": level_info,
    }


def validate_entry_amount(
    user: User,
    mode_config: TriviaModeConfig,
) -> tuple[bool, str]:
    """
    Check if user has paid the required entry amount.

    Args:
        user: User object
        mode_config: Mode configuration

    Returns:
        Tuple of (is_valid, error_message)
    """
    if mode_config.amount <= 0:
        return True, ""  # Free mode

    # Check user's wallet balance
    wallet_balance = (
        user.wallet_balance_minor
        if hasattr(user, "wallet_balance_minor")
        and user.wallet_balance_minor is not None
        else int((user.wallet_balance or 0) * 100)
    )
    required_amount_minor = int(mode_config.amount * 100)

    if wallet_balance < required_amount_minor:
        return False, f"Insufficient balance. Required: ${mode_config.amount:.2f}"

    return True, ""


def get_correct_answer_letter(question: Any) -> str:
    """
    Normalize the question's correct answer to a letter (A-D).
    Handles values such as 'option_a', 'Option A', or the textual option value.
    """
    if not question:
        return ""

    raw = getattr(question, "correct_answer", "") or ""
    normalized = raw.strip().lower()
    if not normalized:
        return ""

    letter_map = {
        "option_a": "A",
        "option_b": "B",
        "option_c": "C",
        "option_d": "D",
    }

    if normalized in {"a", "b", "c", "d"}:
        return normalized.lower()
    if normalized in letter_map:
        return letter_map[normalized].lower()

    for attr, letter in letter_map.items():
        value = getattr(question, attr, "")
        if value and value.strip().lower() == normalized:
            return letter.lower()
    return normalized.lower()


def check_question_duplicate(db: Session, question_text: str, mode_id: str) -> bool:
    """
    Check if a question already exists in the mode.

    Args:
        db: Database session
        question_text: Question text to check
        mode_id: Mode identifier

    Returns:
        True if duplicate exists
    """
    from utils.question_hash_utils import (
        check_duplicate_in_mode,
        generate_question_hash,
    )

    question_hash = generate_question_hash(question_text)
    return check_duplicate_in_mode(db, question_hash, mode_id)
