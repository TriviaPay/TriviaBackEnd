"""
Free mode trivia endpoints.
"""

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from db import get_db
from models import (
    TriviaFreeModeLeaderboard,
    TriviaFreeModeWinners,
    TriviaQuestionsFreeMode,
    TriviaQuestionsFreeModeDaily,
    User,
)
from routers.dependencies import get_current_user
from utils.free_mode_rewards import (
    calculate_reward_distribution,
    distribute_rewards_to_winners,
    get_eligible_participants_free_mode,
    rank_participants_by_completion,
)
from utils.trivia_mode_service import (
    get_active_draw_date,
    get_daily_questions_for_mode,
    get_date_range_for_query,
    get_mode_config,
    submit_answer_for_mode,
)

from .schemas import SubmitAnswerRequest
from .service import get_free_mode_questions as service_get_free_mode_questions
from .service import submit_free_mode_answer as service_submit_free_mode_answer

router = APIRouter(prefix="/trivia/free-mode", tags=["trivia-free-mode"])
logger = logging.getLogger(__name__)


@router.get("/questions")
async def get_free_mode_questions(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Get today's 3 questions for free mode.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return service_get_free_mode_questions(db, user)


@router.get("/current-question")
async def get_current_free_mode_question(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Get the current question the user should answer next.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    questions = get_daily_questions_for_mode(db, "free_mode", user)

    if not questions:
        target_date = get_active_draw_date()
        start_range, end_range = get_date_range_for_query(target_date)
        daily_allocated = (
            db.query(func.count(TriviaQuestionsFreeModeDaily.id))
            .filter(
                TriviaQuestionsFreeModeDaily.date >= start_range,
                TriviaQuestionsFreeModeDaily.date <= end_range,
            )
            .scalar()
            or 0
        )
        pool_size = db.query(func.count(TriviaQuestionsFreeMode.id)).scalar() or 0
        logger.warning(
            "No questions available for today | "
            f"user_id={user.account_id} | "
            f"target_date={target_date.isoformat()} | "
            f"daily_allocated={daily_allocated} | "
            f"pool_size={pool_size}"
        )
        raise HTTPException(status_code=404, detail="No questions available for today")

    # Find the first unanswered question
    for q in questions:
        if q["status"] in ["locked", "viewed"]:
            return {"question": q}

    # All questions answered
    return {"message": "All questions completed", "questions": questions}


@router.post("/submit-answer")
async def submit_free_mode_answer(
    request: SubmitAnswerRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Submit answer for a free mode question.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    result = service_submit_free_mode_answer(
        db, user, request.question_id, request.answer
    )

    if result["status"] == "error":
        raise HTTPException(
            status_code=400, detail=result.get("message", "Error submitting answer")
        )

    return result


@router.get("/leaderboard")
async def get_free_mode_leaderboard(
    draw_date: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Get daily leaderboard for free mode.
    Only shows daily winners (no weekly/all-time).
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Parse draw_date or use most recent completed draw
    if draw_date:
        try:
            target_date = date.fromisoformat(draw_date)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid date format. Use YYYY-MM-DD"
            )
    else:
        # Get most recent completed draw date
        # Before draw time: yesterday's draw (completed yesterday)
        # After draw time: today's draw (just completed at today's draw time)
        active_date = get_active_draw_date()
        from utils.trivia_mode_service import get_today_in_app_timezone

        today = get_today_in_app_timezone()
        if active_date == today:
            # After draw time, show today's completed draw
            target_date = active_date
        else:
            # Before draw time, show yesterday's completed draw
            target_date = active_date

    # Get leaderboard entries
    leaderboard_entries = (
        db.query(TriviaFreeModeLeaderboard)
        .filter(TriviaFreeModeLeaderboard.draw_date == target_date)
        .order_by(
            TriviaFreeModeLeaderboard.position, TriviaFreeModeLeaderboard.completed_at
        )
        .all()
    )

    # Get user details with profile information
    from utils.chat_helpers import get_user_chat_profile_data_bulk

    user_ids = [entry.account_id for entry in leaderboard_entries]
    users = {}
    if user_ids:
        user_rows = db.query(User).filter(User.account_id.in_(user_ids)).all()
        users = {user.account_id: user for user in user_rows}
    profile_map = get_user_chat_profile_data_bulk(list(users.values()), db)

    result = []
    for entry in leaderboard_entries:
        user_obj = users.get(entry.account_id)
        if not user_obj:
            continue
        profile_data = profile_map.get(entry.account_id, {})
        badge_data = profile_data.get("badge") or {}

        result.append(
            {
                "position": entry.position,
                "username": user_obj.username,
                "user_id": entry.account_id,
                "gems_awarded": entry.gems_awarded,
                "completed_at": (
                    entry.completed_at.isoformat() if entry.completed_at else None
                ),
                "profile_pic": profile_data.get("profile_pic_url"),
                "badge_image_url": badge_data.get("image_url"),
                "avatar_url": profile_data.get("avatar_url"),
                "frame_url": profile_data.get("frame_url"),
                "subscription_badges": profile_data.get("subscription_badges", []),
                "date_won": target_date.isoformat(),
                "level": profile_data.get("level", 1),
                "level_progress": profile_data.get("level_progress", "0/100"),
            }
        )

    return {"draw_date": target_date.isoformat(), "leaderboard": result}


@router.post("/double-gems")
async def double_gems_after_win(
    draw_date: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Watch ad to double gems after winning.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Parse draw_date or use yesterday's draw
    if draw_date:
        try:
            target_date = date.fromisoformat(draw_date)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid date format. Use YYYY-MM-DD"
            )
    else:
        target_date = get_active_draw_date() - date.resolution  # Yesterday's draw

    # Find winner record
    winner = (
        db.query(TriviaFreeModeWinners)
        .filter(
            TriviaFreeModeWinners.account_id == user.account_id,
            TriviaFreeModeWinners.draw_date == target_date,
        )
        .first()
    )

    if not winner:
        raise HTTPException(
            status_code=404, detail="You are not a winner for this draw date"
        )

    if winner.double_gems_flag:
        raise HTTPException(
            status_code=400, detail="You have already doubled your gems for this draw"
        )

    # Double the gems
    doubled_gems = winner.gems_awarded * 2
    winner.double_gems_flag = True
    winner.final_gems = doubled_gems

    # Add doubled gems to user balance
    user.gems += (
        winner.gems_awarded
    )  # Add the additional gems (already got gems_awarded)

    db.commit()

    return {
        "success": True,
        "original_gems": winner.gems_awarded,
        "doubled_gems": doubled_gems,
        "total_gems": user.gems,
        "message": f"Successfully doubled your gems! You now have {doubled_gems} gems for this draw.",
    }


@router.get("/status")
async def get_free_mode_status(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Get user's status for free mode (progress, completion time, etc.).
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    target_date = get_active_draw_date()

    # Get user's attempts
    from models import TriviaUserFreeModeDaily

    user_attempts = (
        db.query(TriviaUserFreeModeDaily)
        .filter(
            TriviaUserFreeModeDaily.account_id == user.account_id,
            TriviaUserFreeModeDaily.date == target_date,
        )
        .order_by(TriviaUserFreeModeDaily.question_order)
        .all()
    )

    # Count answers and correct answers - ensure all 3 questions (order 1, 2, 3) are checked
    # Filter to only the first 3 questions
    first_three_attempts = [a for a in user_attempts if a.question_order in [1, 2, 3]]

    # Count how many questions have been answered (regardless of correctness)
    answered_attempts = [
        a
        for a in first_three_attempts
        if a.status in ["answered_correct", "answered_wrong"]
    ]
    questions_answered = len(answered_attempts)

    # Count how many are correct (use == True instead of is True for database booleans)
    correct_attempts = [
        a
        for a in first_three_attempts
        if a.is_correct == True and a.status == "answered_correct"
    ]
    correct_count = len(correct_attempts)

    # Verify we have exactly 3 correct answers for questions 1, 2, 3
    correct_question_orders = {a.question_order for a in correct_attempts}
    all_three_completed_correctly = correct_count == 3 and correct_question_orders == {
        1,
        2,
        3,
    }

    total_questions = 3

    # Get completion time
    third_question = next(
        (
            a
            for a in user_attempts
            if a.question_order == 3 and a.third_question_completed_at
        ),
        None,
    )

    # Get user's answers (fill_in_answer for each question)
    answers = []
    for attempt in sorted(user_attempts, key=lambda x: x.question_order):
        answers.append(
            {
                "question_order": attempt.question_order,
                "user_answer": attempt.user_answer,
                "is_correct": attempt.is_correct,
                "answered_at": (
                    attempt.answered_at.isoformat() if attempt.answered_at else None
                ),
            }
        )

    # Check if user is a winner for the most recent completed draw
    # Before draw time: check yesterday's draw
    # After draw time: check today's draw (just completed)
    from utils.trivia_mode_service import get_today_in_app_timezone

    today = get_today_in_app_timezone()
    if target_date == today:
        # After draw time, check today's completed draw
        winner_draw_date = target_date
    else:
        # Before draw time, check yesterday's completed draw
        winner_draw_date = target_date
    is_winner = (
        db.query(TriviaFreeModeWinners)
        .filter(
            TriviaFreeModeWinners.account_id == user.account_id,
            TriviaFreeModeWinners.draw_date == winner_draw_date,
        )
        .first()
        is not None
    )

    return {
        "progress": {
            "questions_answered": questions_answered,  # How many questions have been answered (0-3)
            "correct_answers": correct_count,  # How many are correct (0-3)
            "total_questions": total_questions,
            "completed": all_three_completed_correctly,  # True only if all 3 questions answered correctly
            "all_questions_answered": questions_answered
            == total_questions,  # True if all questions attempted (regardless of correctness)
        },
        "completion_time": (
            third_question.third_question_completed_at.isoformat()
            if third_question
            else None
        ),
        "is_winner": is_winner,
        "current_date": target_date.isoformat(),
        "fill_in_answer": answers,  # User's submitted answers for each question
    }
