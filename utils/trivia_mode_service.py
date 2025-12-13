"""
Generic service for trivia mode operations.
"""
import os
import json
import logging
from typing import Optional, Dict, Any, List
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, func
import pytz
from models import (
    TriviaModeConfig, TriviaQuestionsFreeMode, TriviaQuestionsFreeModeDaily,
    TriviaUserFreeModeDaily, User
)

logger = logging.getLogger(__name__)


def get_mode_config(db: Session, mode_id: str) -> Optional[TriviaModeConfig]:
    """
    Get mode configuration by mode_id.
    
    Args:
        db: Database session
        mode_id: The mode identifier
        
    Returns:
        TriviaModeConfig object or None if not found
    """
    return db.query(TriviaModeConfig).filter(
        TriviaModeConfig.mode_id == mode_id
    ).first()


def get_today_in_app_timezone() -> date:
    """Get today's date in the app's timezone (EST/US Eastern)."""
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    return now.date()


def get_active_draw_date() -> date:
    """
    Get the draw date for which users should see questions.
    
    Logic:
    - Before draw time: return yesterday's date
    - After draw time: return today's date
    - After 12 AM (midnight): return yesterday again until draw time
    
    This ensures that:
    - Before draw time, users see questions for the current draw period (yesterday's date)
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
    draw_time = now.replace(hour=draw_time_hour, minute=draw_time_minute, second=0, microsecond=0)
    
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
    import os
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    
    # Start of day in app timezone
    start_local = tz.localize(datetime.combine(target_date, datetime.min.time()))
    start_utc = start_local.astimezone(pytz.UTC)
    
    # End of day in app timezone
    end_local = tz.localize(datetime.combine(target_date, datetime.max.time()))
    end_utc = end_local.astimezone(pytz.UTC)
    
    return start_utc, end_utc


def get_daily_questions_for_mode(
    db: Session,
    mode_id: str,
    user: User,
    target_date: Optional[date] = None
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
    
    # Get mode config
    mode_config = get_mode_config(db, mode_id)
    if not mode_config:
        return []
    
    # Get questions based on mode
    if mode_id == 'free_mode':
        return get_free_mode_questions(db, user, target_date)
    
    # Add more modes here
    return []


def get_free_mode_questions(db: Session, user: User, target_date: date) -> List[Dict[str, Any]]:
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
    daily_pool = db.query(TriviaQuestionsFreeModeDaily).options(
        joinedload(TriviaQuestionsFreeModeDaily.question)
    ).filter(
        TriviaQuestionsFreeModeDaily.date >= start_datetime,
        TriviaQuestionsFreeModeDaily.date <= end_datetime
    ).order_by(TriviaQuestionsFreeModeDaily.question_order).all()
    
    # Auto-allocate questions if pool is empty
    if not daily_pool:
        logger.info(f"No questions found for date {target_date}, attempting auto-allocation...")
        try:
            # Get mode config
            mode_config = get_mode_config(db, 'free_mode')
            if not mode_config:
                logger.error("Free mode config not found, cannot auto-allocate questions")
                # Try to create default config
                try:
                    mode_config = TriviaModeConfig(
                        mode_id='free_mode',
                        mode_name='Free Mode',
                        questions_count=3,
                        reward_distribution='{"winner_count_formula": "tiered", "tiered_config": {"default": 1}, "gem_shares": [100]}',
                        amount=0.0,
                        leaderboard_types='["daily"]',
                        ad_config='{}',
                        survey_config='{}'
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
            unused_questions = db.query(TriviaQuestionsFreeMode).filter(
                TriviaQuestionsFreeMode.is_used == False
            ).all()
            
            logger.info(f"Found {len(unused_questions)} unused questions")
            
            # If not enough unused questions, get any questions
            import random
            if len(unused_questions) < questions_count:
                all_questions = db.query(TriviaQuestionsFreeMode).all()
                logger.info(f"Not enough unused questions, using all {len(all_questions)} questions")
                if len(all_questions) >= questions_count:
                    available_questions = random.sample(all_questions, questions_count)
                else:
                    available_questions = all_questions
                    logger.warning(f"Only {len(all_questions)} questions available, need {questions_count}")
            else:
                available_questions = random.sample(unused_questions, questions_count)
            
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
                    is_used=False
                )
                db.add(daily_question)
                # Mark question as used
                question.is_used = True
            
            db.commit()
            logger.info(f"Successfully allocated {len(available_questions)} questions")
            
            # Re-query the daily pool with eager loading
            daily_pool = db.query(TriviaQuestionsFreeModeDaily).options(
                joinedload(TriviaQuestionsFreeModeDaily.question)
            ).filter(
                TriviaQuestionsFreeModeDaily.date >= start_datetime,
                TriviaQuestionsFreeModeDaily.date <= end_datetime
            ).order_by(TriviaQuestionsFreeModeDaily.question_order).all()
            
            logger.info(f"Re-queried daily pool, found {len(daily_pool)} questions")
        except Exception as e:
            db.rollback()
            logger.error(f"Error during auto-allocation: {str(e)}", exc_info=True)
            # Don't return empty, try to continue with whatever we have
    
    # Get user's attempts
    user_attempts = {
        (ud.date, ud.question_order): ud
        for ud in db.query(TriviaUserFreeModeDaily).filter(
            TriviaUserFreeModeDaily.account_id == user.account_id,
            TriviaUserFreeModeDaily.date == target_date
        ).all()
    }
    
    questions = []
    for dq in daily_pool:
        # Eagerly load the question relationship
        if not hasattr(dq, 'question') or dq.question is None:
            # If relationship not loaded, query directly
            question = db.query(TriviaQuestionsFreeMode).filter(
                TriviaQuestionsFreeMode.id == dq.question_id
            ).first()
            if not question:
                logger.warning(f"Question {dq.question_id} not found for daily pool entry {dq.id}")
                continue
        else:
            question = dq.question
        
        user_attempt = user_attempts.get((target_date, dq.question_order))
        
        question_data = {
            'question_id': dq.question_id,
            'question_order': dq.question_order,
            'question': question.question,
            'option_a': question.option_a,
            'option_b': question.option_b,
            'option_c': question.option_c,
            'option_d': question.option_d,
            'correct_answer': question.correct_answer,
            'hint': question.hint,
            'fill_in_answer': user_attempt.user_answer if user_attempt and user_attempt.user_answer else None,  # User's submitted answer
            'explanation': question.explanation,
            'category': question.category,
            'difficulty_level': question.difficulty_level,
            'picture_url': question.picture_url,
            'status': user_attempt.status if user_attempt else 'locked',
            'is_correct': user_attempt.is_correct if user_attempt else None,
            'answered_at': user_attempt.answered_at.isoformat() if user_attempt and user_attempt.answered_at else None,
        }
        questions.append(question_data)
    
    logger.info(f"Returning {len(questions)} questions for free mode, target_date: {target_date}")
    return questions


def submit_answer_for_mode(
    db: Session,
    mode_id: str,
    user: User,
    question_id: int,
    answer: str,
    target_date: Optional[date] = None
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
    
    if mode_id == 'free_mode':
        return submit_free_mode_answer(db, user, question_id, answer, target_date)
    
    return {'status': 'error', 'message': f'Unknown mode: {mode_id}'}


def submit_free_mode_answer(
    db: Session,
    user: User,
    question_id: int,
    answer: str,
    target_date: date
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
    question = db.query(TriviaQuestionsFreeMode).filter(
        TriviaQuestionsFreeMode.id == question_id
    ).first()
    
    if not question:
        return {'status': 'error', 'message': 'Question not found', 'is_correct': False}
    
    # Find or create user attempt
    start_datetime, end_datetime = get_date_range_for_query(target_date)
    daily_q = db.query(TriviaQuestionsFreeModeDaily).filter(
        TriviaQuestionsFreeModeDaily.date >= start_datetime,
        TriviaQuestionsFreeModeDaily.date <= end_datetime,
        TriviaQuestionsFreeModeDaily.question_id == question_id
    ).first()
    
    if not daily_q:
        return {'status': 'error', 'message': 'Question not available for today', 'is_correct': False}
    
    user_attempt = db.query(TriviaUserFreeModeDaily).filter(
        TriviaUserFreeModeDaily.account_id == user.account_id,
        TriviaUserFreeModeDaily.date == target_date,
        TriviaUserFreeModeDaily.question_order == daily_q.question_order
    ).first()
    
    if not user_attempt:
        # Create new attempt
        user_attempt = TriviaUserFreeModeDaily(
            account_id=user.account_id,
            date=target_date,
            question_order=daily_q.question_order,
            question_id=question_id,
            status='viewed'
        )
        db.add(user_attempt)
    
    # Check if already answered
    if user_attempt.status in ['answered_correct', 'answered_wrong']:
        return {
            'status': 'error',
            'message': f'Question already answered ({user_attempt.status})',
            'is_correct': user_attempt.is_correct
        }
    
    # Check answer
    is_correct = answer.strip().upper() == question.correct_answer.strip().upper()
    
    # Update attempt
    user_attempt.user_answer = answer
    user_attempt.is_correct = is_correct
    user_attempt.answered_at = datetime.utcnow()
    user_attempt.status = 'answered_correct' if is_correct else 'answered_wrong'
    
    # If this is the 3rd question and it's correct, set completion time
    if is_correct and daily_q.question_order == 3:
        user_attempt.third_question_completed_at = datetime.utcnow()
    
    db.commit()
    
    # Track answer and update user level
    from utils.user_level_service import track_answer_and_update_level
    level_info = track_answer_and_update_level(user, db)
    
    return {
        'status': 'success',
        'is_correct': is_correct,
        'message': 'Correct!' if is_correct else 'Incorrect. Try again tomorrow!',
        'level_info': level_info
    }


def validate_entry_amount(user: User, mode_config: TriviaModeConfig) -> tuple[bool, str]:
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
    wallet_balance = user.wallet_balance_minor if hasattr(user, 'wallet_balance_minor') and user.wallet_balance_minor is not None else int((user.wallet_balance or 0) * 100)
    required_amount_minor = int(mode_config.amount * 100)
    
    if wallet_balance < required_amount_minor:
        return False, f"Insufficient balance. Required: ${mode_config.amount:.2f}"
    
    return True, ""


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
    from utils.question_hash_utils import generate_question_hash, check_duplicate_in_mode
    
    question_hash = generate_question_hash(question_text)
    return check_duplicate_in_mode(db, question_hash, mode_id)

