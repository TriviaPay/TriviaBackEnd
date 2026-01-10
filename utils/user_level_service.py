"""
Service for tracking user level based on trivia questions answered correctly.
Level increases by 1 for every 100 CORRECT answers across all modes.
"""

import logging
from typing import Dict, List, Optional

from sqlalchemy import and_, func, select, union_all
from sqlalchemy.orm import Session

from models import (
    TriviaUserBronzeModeDaily,
    TriviaUserFreeModeDaily,
    TriviaUserSilverModeDaily,
    User,
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
    free_query = select(TriviaUserFreeModeDaily.account_id).where(
        TriviaUserFreeModeDaily.account_id == user.account_id,
        TriviaUserFreeModeDaily.status == "answered_correct",
        TriviaUserFreeModeDaily.is_correct.is_(True),
    )
    bronze_query = select(TriviaUserBronzeModeDaily.account_id).where(
        TriviaUserBronzeModeDaily.account_id == user.account_id,
        TriviaUserBronzeModeDaily.submitted_at.isnot(None),
        TriviaUserBronzeModeDaily.is_correct.is_(True),
    )
    silver_query = select(TriviaUserSilverModeDaily.account_id).where(
        TriviaUserSilverModeDaily.account_id == user.account_id,
        TriviaUserSilverModeDaily.submitted_at.isnot(None),
        TriviaUserSilverModeDaily.is_correct.is_(True),
    )

    union_stmt = union_all(
        free_query,
        bronze_query,
        silver_query,
    ).alias("correct_answers")
    count_stmt = select(func.count()).select_from(union_stmt)
    total_count = db.execute(count_stmt).scalar() or 0

    logger.debug(
        f"User {user.account_id} has {total_count} CORRECT answers "
        f"(computed via union query)"
    )

    return total_count


def get_level_progress(
    user: User,
    db: Session,
    total_correct: Optional[int] = None,
) -> dict:
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
    if total_correct is None:
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
        "level": current_level,
        "current_correct_answers": current_level_correct,
        "target_correct_answers": target_correct,
        "progress": progress,
        "total_correct_answers": total_correct,
    }


def get_level_progress_for_users(
    users: List[User],
    db: Session,
) -> Dict[int, Dict[str, object]]:
    """
    Batch version of get_level_progress for multiple users.
    Returns a mapping of account_id to level info.
    """
    if not users:
        return {}

    user_ids = [user.account_id for user in users]

    free_counts = dict(
        db.query(
            TriviaUserFreeModeDaily.account_id,
            func.count(TriviaUserFreeModeDaily.account_id),
        )
        .filter(
            and_(
                TriviaUserFreeModeDaily.account_id.in_(user_ids),
                TriviaUserFreeModeDaily.status == "answered_correct",
                TriviaUserFreeModeDaily.is_correct.is_(True),
            )
        )
        .group_by(TriviaUserFreeModeDaily.account_id)
        .all()
    )

    bronze_counts = dict(
        db.query(
            TriviaUserBronzeModeDaily.account_id,
            func.count(TriviaUserBronzeModeDaily.account_id),
        )
        .filter(
            and_(
                TriviaUserBronzeModeDaily.account_id.in_(user_ids),
                TriviaUserBronzeModeDaily.submitted_at.isnot(None),
                TriviaUserBronzeModeDaily.is_correct.is_(True),
            )
        )
        .group_by(TriviaUserBronzeModeDaily.account_id)
        .all()
    )

    silver_counts = dict(
        db.query(
            TriviaUserSilverModeDaily.account_id,
            func.count(TriviaUserSilverModeDaily.account_id),
        )
        .filter(
            and_(
                TriviaUserSilverModeDaily.account_id.in_(user_ids),
                TriviaUserSilverModeDaily.submitted_at.isnot(None),
                TriviaUserSilverModeDaily.is_correct.is_(True),
            )
        )
        .group_by(TriviaUserSilverModeDaily.account_id)
        .all()
    )

    results: Dict[int, Dict[str, object]] = {}
    for user in users:
        total_correct = (
            free_counts.get(user.account_id, 0)
            + bronze_counts.get(user.account_id, 0)
            + silver_counts.get(user.account_id, 0)
        )
        current_level = user.level if user.level else 1
        current_level_correct = total_correct - ((current_level - 1) * 100)
        if current_level_correct < 0:
            current_level_correct = 0
        progress = f"{current_level_correct}/100"
        results[user.account_id] = {
            "level": current_level,
            "level_progress": progress,
            "total_correct_answers": total_correct,
        }

    return results


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
        logger.info(
            f"User {user.account_id} leveled up from {current_level} "
            f"to {expected_level} ({total_correct} correct answers)"
        )

    # Calculate correct answers until next level
    correct_until_next = 100 - (total_correct % 100)

    # Get progress info
    progress = get_level_progress(user, db, total_correct=total_correct)

    return {
        "level_increased": level_increased,
        "new_level": user.level,
        "total_correct_answers": total_correct,
        "correct_answers_until_next_level": correct_until_next,
        "progress": progress,
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
