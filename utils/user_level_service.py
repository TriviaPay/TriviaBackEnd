"""
Service for tracking user level based on trivia questions answered correctly.
Level increases by 1 for every 100 CORRECT answers across all modes.
"""
import logging
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from models import (
    User,
    TriviaUserFreeModeDaily,
    TriviaUserBronzeModeDaily,
    TriviaUserSilverModeDaily
)
# TriviaUserDaily removed - legacy table deleted

logger = logging.getLogger(__name__)


def count_total_correct_answers(user: User, db: Session) -> int:
    """
    Count total CORRECT questions answered by user across all trivia modes.
    Only counts correct answers, not wrong answers.
    
    Args:
        user: User object
        db: Database session
        
    Returns:
        Total count of CORRECT questions answered
    """
    total_count = 0
    
    # Count free mode CORRECT answers only
    free_mode_count = db.query(func.count(TriviaUserFreeModeDaily.account_id)).filter(
        and_(
            TriviaUserFreeModeDaily.account_id == user.account_id,
            TriviaUserFreeModeDaily.status == 'answered_correct',
            TriviaUserFreeModeDaily.is_correct == True
        )
    ).scalar() or 0
    
    # Count bronze mode CORRECT answers only
    bronze_mode_count = db.query(func.count(TriviaUserBronzeModeDaily.account_id)).filter(
        and_(
            TriviaUserBronzeModeDaily.account_id == user.account_id,
            TriviaUserBronzeModeDaily.submitted_at.isnot(None),
            TriviaUserBronzeModeDaily.is_correct == True
        )
    ).scalar() or 0
    
    # Count silver mode CORRECT answers only
    silver_mode_count = db.query(func.count(TriviaUserSilverModeDaily.account_id)).filter(
        and_(
            TriviaUserSilverModeDaily.account_id == user.account_id,
            TriviaUserSilverModeDaily.submitted_at.isnot(None),
            TriviaUserSilverModeDaily.is_correct == True
        )
    ).scalar() or 0
    
    # Legacy TriviaUserDaily removed - only count mode-specific tables
    total_count = free_mode_count + bronze_mode_count + silver_mode_count
    
    logger.debug(f"User {user.account_id} has {total_count} CORRECT answers total "
                f"(free: {free_mode_count}, bronze: {bronze_mode_count}, "
                f"silver: {silver_mode_count})")
    
    return total_count


def get_level_progress(user: User, db: Session) -> dict:
    """
    Get user's level progress information.
    
    Args:
        user: User object
        db: Database session
        
    Returns:
        Dictionary with:
        - level: int (current level)
        - current_correct_answers: int (correct answers for current level)
        - target_correct_answers: int (target for next level)
        - progress: str (e.g., "2/100", "120/200", "430/500")
    """
    current_level = user.level if user.level else 1
    total_correct = count_total_correct_answers(user, db)
    
    # Calculate correct answers for current level (total - (level-1)*100)
    current_level_correct = total_correct - ((current_level - 1) * 100)
    if current_level_correct < 0:
        current_level_correct = 0
    
    # Target for next level is always 100
    target_correct = 100
    
    # Progress string: current/target
    progress = f"{current_level_correct}/{target_correct}"
    
    return {
        'level': current_level,
        'current_correct_answers': current_level_correct,
        'target_correct_answers': target_correct,
        'progress': progress,
        'total_correct_answers': total_correct
    }


def update_user_level(user: User, db: Session) -> dict:
    """
    Update user level based on total CORRECT questions answered.
    Level increases by 1 for every 100 CORRECT answers.
    
    Args:
        user: User object
        db: Database session
        
    Returns:
        Dictionary with:
        - level_increased: bool
        - new_level: int
        - total_correct_answers: int
        - correct_answers_until_next_level: int
        - progress: dict (level progress info)
    """
    # Get current level (default to 1 if None)
    current_level = user.level if user.level else 1
    
    # Count total CORRECT questions answered
    total_correct = count_total_correct_answers(user, db)
    
    # Calculate what level should be (1 + floor(total_correct / 100))
    expected_level = 1 + (total_correct // 100)
    
    level_increased = False
    if expected_level > current_level:
        # Update user level
        user.level = expected_level
        db.commit()
        db.refresh(user)
        level_increased = True
        logger.info(f"User {user.account_id} leveled up from {current_level} to {expected_level} "
                   f"({total_correct} correct answers)")
    
    # Calculate correct answers until next level
    correct_until_next = 100 - (total_correct % 100)
    
    # Get progress info
    progress = get_level_progress(user, db)
    
    return {
        'level_increased': level_increased,
        'new_level': user.level,
        'total_correct_answers': total_correct,
        'correct_answers_until_next_level': correct_until_next,
        'progress': progress
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

