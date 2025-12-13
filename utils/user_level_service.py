"""
Service for tracking user level based on trivia questions answered.
Level increases by 1 for every 100 questions answered (correct or wrong) across all modes.
"""
import logging
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from models import (
    User,
    TriviaUserFreeModeDaily,
    TriviaUserBronzeModeDaily,
    TriviaUserSilverModeDaily,
    TriviaUserDaily
)

logger = logging.getLogger(__name__)


def count_total_questions_answered(user: User, db: Session) -> int:
    """
    Count total questions answered by user across all trivia modes.
    Counts both correct and wrong answers.
    
    Args:
        user: User object
        db: Database session
        
    Returns:
        Total count of questions answered
    """
    total_count = 0
    
    # Count free mode answers (where user has answered)
    free_mode_count = db.query(func.count(TriviaUserFreeModeDaily.account_id)).filter(
        and_(
            TriviaUserFreeModeDaily.account_id == user.account_id,
            or_(
                TriviaUserFreeModeDaily.status == 'answered_correct',
                TriviaUserFreeModeDaily.status == 'answered_wrong'
            )
        )
    ).scalar() or 0
    
    # Count bronze mode answers (where submitted_at is not null)
    bronze_mode_count = db.query(func.count(TriviaUserBronzeModeDaily.account_id)).filter(
        and_(
            TriviaUserBronzeModeDaily.account_id == user.account_id,
            TriviaUserBronzeModeDaily.submitted_at.isnot(None)
        )
    ).scalar() or 0
    
    # Count silver mode answers (where submitted_at is not null)
    silver_mode_count = db.query(func.count(TriviaUserSilverModeDaily.account_id)).filter(
        and_(
            TriviaUserSilverModeDaily.account_id == user.account_id,
            TriviaUserSilverModeDaily.submitted_at.isnot(None)
        )
    ).scalar() or 0
    
    # Count legacy trivia answers (where status is answered_correct or answered_wrong)
    legacy_count = db.query(func.count(TriviaUserDaily.account_id)).filter(
        and_(
            TriviaUserDaily.account_id == user.account_id,
            or_(
                TriviaUserDaily.status == 'answered_correct',
                TriviaUserDaily.status == 'answered_wrong'
            )
        )
    ).scalar() or 0
    
    total_count = free_mode_count + bronze_mode_count + silver_mode_count + legacy_count
    
    logger.debug(f"User {user.account_id} has answered {total_count} questions total "
                f"(free: {free_mode_count}, bronze: {bronze_mode_count}, "
                f"silver: {silver_mode_count}, legacy: {legacy_count})")
    
    return total_count


def update_user_level(user: User, db: Session) -> dict:
    """
    Update user level based on total questions answered.
    Level increases by 1 for every 100 questions answered.
    
    Args:
        user: User object
        db: Database session
        
    Returns:
        Dictionary with:
        - level_increased: bool
        - new_level: int
        - total_questions: int
        - questions_until_next_level: int
    """
    # Get current level (default to 1 if None)
    current_level = user.level if user.level else 1
    
    # Count total questions answered
    total_questions = count_total_questions_answered(user, db)
    
    # Calculate what level should be (1 + floor(total_questions / 100))
    expected_level = 1 + (total_questions // 100)
    
    level_increased = False
    if expected_level > current_level:
        # Update user level
        user.level = expected_level
        db.commit()
        db.refresh(user)
        level_increased = True
        logger.info(f"User {user.account_id} leveled up from {current_level} to {expected_level} "
                   f"(answered {total_questions} questions)")
    
    # Calculate questions until next level
    questions_until_next_level = 100 - (total_questions % 100)
    
    return {
        'level_increased': level_increased,
        'new_level': user.level,
        'total_questions': total_questions,
        'questions_until_next_level': questions_until_next_level
    }


def track_answer_and_update_level(user: User, db: Session) -> dict:
    """
    Track that user answered a question and update level if needed.
    This should be called after a user submits an answer.
    
    Args:
        user: User object
        db: Database session
        
    Returns:
        Dictionary with level update information
    """
    return update_user_level(user, db)

