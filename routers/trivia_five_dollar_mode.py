"""
Bronze Mode ($5) trivia endpoints.
Requires active $5 monthly subscription to access.
"""
import logging
import random
from typing import Optional
from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from db import get_db
from routers.dependencies import get_current_user
from models import (
    User, TriviaBronzeModeWinners, TriviaBronzeModeLeaderboard,
    TriviaQuestionsBronzeModeDaily, TriviaUserBronzeModeDaily,
    TriviaQuestionsBronzeMode
)
from utils.trivia_mode_service import get_active_draw_date, get_mode_config, get_date_range_for_query
from utils.subscription_service import check_mode_access
from models import TriviaModeConfig
import json
from sqlalchemy.orm import joinedload

router = APIRouter(prefix="/trivia/bronze-mode", tags=["trivia-bronze-mode"])
logger = logging.getLogger(__name__)


class SubmitAnswerRequest(BaseModel):
    question_id: int
    answer: str


def ensure_bronze_mode_config(db: Session) -> TriviaModeConfig:
    """
    Ensure bronze mode configuration exists, creating it if missing.
    
    Args:
        db: Database session
        
    Returns:
        TriviaModeConfig object
        
    Raises:
        HTTPException: If config cannot be created
    """
    mode_config = get_mode_config(db, 'bronze')
    if not mode_config:
        try:
            reward_distribution = {
                "reward_type": "money",
                "distribution_method": "harmonic_sum",
                "requires_subscription": True,
                "subscription_amount": 5.0,
                "profit_share_percentage": 0.5
            }
            mode_config = TriviaModeConfig(
                mode_id='bronze',
                mode_name='Bronze Mode - First-Come Reward',
                questions_count=1,
                reward_distribution=json.dumps(reward_distribution),
                amount=5.0,
                leaderboard_types=json.dumps(['daily']),
                ad_config=json.dumps({}),
                survey_config=json.dumps({})
            )
            db.add(mode_config)
            db.commit()
            db.refresh(mode_config)
            logger.info("Auto-created bronze mode config")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to auto-create bronze mode config: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="Mode configuration not found and could not be created"
            )
    return mode_config


@router.get("/question")
async def get_bronze_mode_question(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get today's question for bronze mode.
    Requires active $5 subscription.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Ensure mode config exists
    ensure_bronze_mode_config(db)
    
    # Check subscription access
    access_check = check_mode_access(db, user, 'bronze')
    if not access_check['has_access']:
        raise HTTPException(
            status_code=403,
            detail=access_check['message']
        )
    
    target_date = get_active_draw_date()
    start_datetime, end_datetime = get_date_range_for_query(target_date)
    
    # Get today's question
    daily_question = db.query(TriviaQuestionsBronzeModeDaily).options(
        joinedload(TriviaQuestionsBronzeModeDaily.question)
    ).filter(
        TriviaQuestionsBronzeModeDaily.date >= start_datetime,
        TriviaQuestionsBronzeModeDaily.date <= end_datetime
    ).first()
    
    # Auto-allocate question if pool is empty
    if not daily_question:
        logger.info(f"No question allocated for {target_date}, attempting auto-allocation...")
        try:
            # Get available questions (prefer unused)
            unused_questions = db.query(TriviaQuestionsBronzeMode).filter(
                TriviaQuestionsBronzeMode.is_used == False
            ).all()
            
            if len(unused_questions) < 1:
                all_questions = db.query(TriviaQuestionsBronzeMode).all()
                if len(all_questions) < 1:
                    raise HTTPException(
                        status_code=404,
                        detail="No questions available in the question pool. Please add questions first."
                    )
                selected_question = random.choice(all_questions)
            else:
                selected_question = random.choice(unused_questions)
            
            # Allocate question to daily pool
            daily_question = TriviaQuestionsBronzeModeDaily(
                date=start_datetime,
                question_id=selected_question.id,
                question_order=1,  # Always 1 for bronze mode
                is_used=False
            )
            db.add(daily_question)
            # Mark question as used
            selected_question.is_used = True
            db.commit()
            db.refresh(daily_question)
            
            # Reload with relationship
            daily_question = db.query(TriviaQuestionsBronzeModeDaily).options(
                joinedload(TriviaQuestionsBronzeModeDaily.question)
            ).filter(TriviaQuestionsBronzeModeDaily.id == daily_question.id).first()
            
            logger.info(f"Auto-allocated question {selected_question.id} for {target_date}")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to auto-allocate question: {str(e)}")
            raise HTTPException(
                status_code=404,
                detail="No question available for today and auto-allocation failed"
            )
    
    # Get user's attempt
    user_attempt = db.query(TriviaUserBronzeModeDaily).filter(
        TriviaUserBronzeModeDaily.account_id == user.account_id,
        TriviaUserBronzeModeDaily.date == target_date
    ).first()
    
    question = daily_question.question
    
    # Check if question is still open (before draw time)
    from utils.trivia_mode_service import get_today_in_app_timezone
    import pytz
    import os
    
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    
    # Get draw time
    draw_time_hour = int(os.getenv("DRAW_TIME_HOUR", "18"))
    draw_time_minute = int(os.getenv("DRAW_TIME_MINUTE", "0"))
    draw_time = now.replace(hour=draw_time_hour, minute=draw_time_minute, second=0, microsecond=0)
    
    is_open = now < draw_time
    time_until_close = (draw_time - now).total_seconds() if is_open else 0
    
    question_data = {
        'question_id': question.id,
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
        'submitted_at': user_attempt.submitted_at.isoformat() if user_attempt and user_attempt.submitted_at else None,
        'is_open': is_open,
        'time_until_close_seconds': int(time_until_close) if time_until_close > 0 else 0
    }
    
    return {"question": question_data}


@router.post("/submit-answer")
async def submit_bronze_mode_answer(
    request: SubmitAnswerRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Submit answer for bronze mode question.
    Only one submission per day per user allowed.
    Tracks submission time for ranking.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Ensure mode config exists
    ensure_bronze_mode_config(db)
    
    # Check subscription access
    access_check = check_mode_access(db, user, 'bronze')
    if not access_check['has_access']:
        raise HTTPException(
            status_code=403,
            detail=access_check['message']
        )
    
    target_date = get_active_draw_date()
    
    # Check if user already submitted today
    existing_attempt = db.query(TriviaUserBronzeModeDaily).filter(
        TriviaUserBronzeModeDaily.account_id == user.account_id,
        TriviaUserBronzeModeDaily.date == target_date
    ).first()
    
    if existing_attempt and existing_attempt.submitted_at:
        raise HTTPException(
            status_code=400,
            detail="You have already submitted an answer for today"
        )
    
    # Get the question
    question = db.query(TriviaQuestionsBronzeMode).filter(
        TriviaQuestionsBronzeMode.id == request.question_id
    ).first()
    
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Verify question is for today
    start_datetime, end_datetime = get_date_range_for_query(target_date)
    daily_q = db.query(TriviaQuestionsBronzeModeDaily).filter(
        TriviaQuestionsBronzeModeDaily.date >= start_datetime,
        TriviaQuestionsBronzeModeDaily.date <= end_datetime,
        TriviaQuestionsBronzeModeDaily.question_id == request.question_id
    ).first()
    
    if not daily_q:
        raise HTTPException(
            status_code=400,
            detail="Question not available for today"
        )
    
    # Check if question is still open
    from utils.trivia_mode_service import get_today_in_app_timezone
    import pytz
    import os
    
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    
    draw_time_hour = int(os.getenv("DRAW_TIME_HOUR", "18"))
    draw_time_minute = int(os.getenv("DRAW_TIME_MINUTE", "0"))
    draw_time = now.replace(hour=draw_time_hour, minute=draw_time_minute, second=0, microsecond=0)
    
    if now >= draw_time:
        raise HTTPException(
            status_code=400,
            detail="Question submission is closed"
        )
    
    # Check if answer is correct
    is_correct = request.answer.strip().lower() == question.correct_answer.strip().lower()
    
    # Create or update user attempt
    if existing_attempt:
        existing_attempt.user_answer = request.answer
        existing_attempt.is_correct = is_correct
        existing_attempt.submitted_at = datetime.utcnow()
        existing_attempt.status = 'answered'
    else:
        user_attempt = TriviaUserBronzeModeDaily(
            account_id=user.account_id,
            date=target_date,
            question_id=request.question_id,
            user_answer=request.answer,
            is_correct=is_correct,
            submitted_at=datetime.utcnow(),
            status='answered'
        )
        db.add(user_attempt)
    
    db.commit()
    
    # Track answer and update user level
    from utils.user_level_service import track_answer_and_update_level
    level_info = track_answer_and_update_level(user, db)
    
    return {
        'status': 'success',
        'is_correct': is_correct,
        'submitted_at': datetime.utcnow().isoformat(),
        'message': 'Answer submitted successfully',
        'level_info': level_info
    }


@router.get("/status")
async def get_bronze_mode_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get user's status for bronze mode (submission status, subscription status).
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Ensure mode config exists
    ensure_bronze_mode_config(db)
    
    target_date = get_active_draw_date()
    
    # Check subscription
    access_check = check_mode_access(db, user, 'bronze')
    
    # Get user's attempt
    user_attempt = db.query(TriviaUserBronzeModeDaily).filter(
        TriviaUserBronzeModeDaily.account_id == user.account_id,
        TriviaUserBronzeModeDaily.date == target_date
    ).first()
    
    # Check if user is a winner for the most recent completed draw
    from utils.trivia_mode_service import get_today_in_app_timezone
    today = get_today_in_app_timezone()
    if target_date == today:
        # After draw time, check today's completed draw
        winner_draw_date = target_date
    else:
        # Before draw time, check yesterday's completed draw
        winner_draw_date = target_date
    is_winner = db.query(TriviaBronzeModeWinners).filter(
        TriviaBronzeModeWinners.account_id == user.account_id,
        TriviaBronzeModeWinners.draw_date == winner_draw_date
    ).first() is not None
    
    return {
        'has_access': access_check['has_access'],
        'subscription_status': access_check['subscription_status'],
        'has_submitted': user_attempt is not None and user_attempt.submitted_at is not None,
        'submitted_at': user_attempt.submitted_at.isoformat() if user_attempt and user_attempt.submitted_at else None,
        'is_correct': user_attempt.is_correct if user_attempt else None,
        'fill_in_answer': user_attempt.user_answer if user_attempt and user_attempt.user_answer else None,  # User's submitted answer
        'is_winner': is_winner,
        'current_date': target_date.isoformat()
    }


@router.get("/leaderboard")
async def get_bronze_mode_leaderboard(
    draw_date: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get daily leaderboard for bronze mode.
    Shows winners ranked by submission time (position) and money awarded.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Parse draw_date or use most recent completed draw
    if draw_date:
        try:
            target_date = date.fromisoformat(draw_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        # Get most recent completed draw date
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
    leaderboard_entries = db.query(TriviaBronzeModeLeaderboard).filter(
        TriviaBronzeModeLeaderboard.draw_date == target_date
    ).order_by(
        TriviaBronzeModeLeaderboard.position,
        TriviaBronzeModeLeaderboard.submitted_at
    ).all()
    
    # Get user details with profile information
    from utils.chat_helpers import get_user_chat_profile_data
    from models import TriviaModeConfig
    
    result = []
    for entry in leaderboard_entries:
        user_obj = db.query(User).filter(User.account_id == entry.account_id).first()
        if user_obj:
            # Get profile data
            profile_data = get_user_chat_profile_data(user_obj, db)
            
            # Get achievement badge image URL
            badge_image_url = None
            if user_obj.badge_id:
                mode_config = db.query(TriviaModeConfig).filter(TriviaModeConfig.mode_id == user_obj.badge_id).first()
                if mode_config and mode_config.badge_image_url:
                    badge_image_url = mode_config.badge_image_url
            
            result.append({
                'position': entry.position,
                'username': user_obj.username,
                'user_id': entry.account_id,
                'money_awarded': entry.money_awarded,
                'submitted_at': entry.submitted_at.isoformat() if entry.submitted_at else None,
                'profile_pic': profile_data.get('profile_pic_url'),
                'badge_image_url': badge_image_url,
                'avatar_url': profile_data.get('avatar_url'),
                'frame_url': profile_data.get('frame_url'),
                'subscription_badges': profile_data.get('subscription_badges', []),
                'date_won': target_date.isoformat(),
                'level': profile_data.get('level', 1),
                'level_progress': profile_data.get('level_progress', '0/100')
            })
    
    return {
        'draw_date': target_date.isoformat(),
        'leaderboard': result
    }

