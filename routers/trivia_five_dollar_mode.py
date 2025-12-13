"""
$5 Mode trivia endpoints.
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
    User, TriviaFiveDollarModeWinners, TriviaFiveDollarModeLeaderboard,
    TriviaQuestionsFiveDollarModeDaily, TriviaUserFiveDollarModeDaily,
    TriviaQuestionsFiveDollarMode
)
from utils.trivia_mode_service import get_active_draw_date, get_mode_config, get_date_range_for_query
from utils.subscription_service import check_mode_access
from models import TriviaModeConfig
import json
from sqlalchemy.orm import joinedload

router = APIRouter(prefix="/trivia/five-dollar-mode", tags=["trivia-five-dollar-mode"])
logger = logging.getLogger(__name__)


class SubmitAnswerRequest(BaseModel):
    question_id: int
    answer: str


def ensure_five_dollar_mode_config(db: Session) -> TriviaModeConfig:
    """
    Ensure $5 mode configuration exists, creating it if missing.
    
    Args:
        db: Database session
        
    Returns:
        TriviaModeConfig object
        
    Raises:
        HTTPException: If config cannot be created
    """
    mode_config = get_mode_config(db, 'five_dollar_mode')
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
                mode_id='five_dollar_mode',
                mode_name='$5 Mode - First-Come Reward',
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
            logger.info("Auto-created $5 mode config")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to auto-create $5 mode config: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="Mode configuration not found and could not be created"
            )
    return mode_config


@router.get("/question")
async def get_five_dollar_mode_question(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get today's question for $5 mode.
    Requires active $5 subscription.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Ensure mode config exists
    ensure_five_dollar_mode_config(db)
    
    # Check subscription access
    access_check = check_mode_access(db, user, 'five_dollar_mode')
    if not access_check['has_access']:
        raise HTTPException(
            status_code=403,
            detail=access_check['message']
        )
    
    target_date = get_active_draw_date()
    start_datetime, end_datetime = get_date_range_for_query(target_date)
    
    # Get today's question
    daily_question = db.query(TriviaQuestionsFiveDollarModeDaily).options(
        joinedload(TriviaQuestionsFiveDollarModeDaily.question)
    ).filter(
        TriviaQuestionsFiveDollarModeDaily.date >= start_datetime,
        TriviaQuestionsFiveDollarModeDaily.date <= end_datetime
    ).first()
    
    # Auto-allocate question if pool is empty
    if not daily_question:
        logger.info(f"No question allocated for {target_date}, attempting auto-allocation...")
        try:
            # Get available questions (prefer unused)
            unused_questions = db.query(TriviaQuestionsFiveDollarMode).filter(
                TriviaQuestionsFiveDollarMode.is_used == False
            ).all()
            
            if len(unused_questions) < 1:
                all_questions = db.query(TriviaQuestionsFiveDollarMode).all()
                if len(all_questions) < 1:
                    raise HTTPException(
                        status_code=404,
                        detail="No questions available in the question pool. Please add questions first."
                    )
                selected_question = random.choice(all_questions)
            else:
                selected_question = random.choice(unused_questions)
            
            # Allocate question to daily pool
            daily_question = TriviaQuestionsFiveDollarModeDaily(
                date=start_datetime,
                question_id=selected_question.id,
                question_order=1,  # Always 1 for $5 mode
                is_used=False
            )
            db.add(daily_question)
            # Mark question as used
            selected_question.is_used = True
            db.commit()
            db.refresh(daily_question)
            
            # Reload with relationship
            daily_question = db.query(TriviaQuestionsFiveDollarModeDaily).options(
                joinedload(TriviaQuestionsFiveDollarModeDaily.question)
            ).filter(TriviaQuestionsFiveDollarModeDaily.id == daily_question.id).first()
            
            logger.info(f"Auto-allocated question {selected_question.id} for {target_date}")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to auto-allocate question: {str(e)}")
            raise HTTPException(
                status_code=404,
                detail="No question available for today and auto-allocation failed"
            )
    
    # Get user's attempt
    user_attempt = db.query(TriviaUserFiveDollarModeDaily).filter(
        TriviaUserFiveDollarModeDaily.account_id == user.account_id,
        TriviaUserFiveDollarModeDaily.date == target_date
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
async def submit_five_dollar_mode_answer(
    request: SubmitAnswerRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Submit answer for $5 mode question.
    Only one submission per day per user allowed.
    Tracks submission time for ranking.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Ensure mode config exists
    ensure_five_dollar_mode_config(db)
    
    # Check subscription access
    access_check = check_mode_access(db, user, 'five_dollar_mode')
    if not access_check['has_access']:
        raise HTTPException(
            status_code=403,
            detail=access_check['message']
        )
    
    target_date = get_active_draw_date()
    
    # Check if user already submitted today
    existing_attempt = db.query(TriviaUserFiveDollarModeDaily).filter(
        TriviaUserFiveDollarModeDaily.account_id == user.account_id,
        TriviaUserFiveDollarModeDaily.date == target_date
    ).first()
    
    if existing_attempt and existing_attempt.submitted_at:
        raise HTTPException(
            status_code=400,
            detail="You have already submitted an answer for today"
        )
    
    # Get the question
    question = db.query(TriviaQuestionsFiveDollarMode).filter(
        TriviaQuestionsFiveDollarMode.id == request.question_id
    ).first()
    
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Verify question is for today
    start_datetime, end_datetime = get_date_range_for_query(target_date)
    daily_q = db.query(TriviaQuestionsFiveDollarModeDaily).filter(
        TriviaQuestionsFiveDollarModeDaily.date >= start_datetime,
        TriviaQuestionsFiveDollarModeDaily.date <= end_datetime,
        TriviaQuestionsFiveDollarModeDaily.question_id == request.question_id
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
        user_attempt = TriviaUserFiveDollarModeDaily(
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
    
    return {
        'status': 'success',
        'is_correct': is_correct,
        'submitted_at': datetime.utcnow().isoformat(),
        'message': 'Answer submitted successfully'
    }


@router.get("/status")
async def get_five_dollar_mode_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get user's status for $5 mode (submission status, subscription status).
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Ensure mode config exists
    ensure_five_dollar_mode_config(db)
    
    target_date = get_active_draw_date()
    
    # Check subscription
    access_check = check_mode_access(db, user, 'five_dollar_mode')
    
    # Get user's attempt
    user_attempt = db.query(TriviaUserFiveDollarModeDaily).filter(
        TriviaUserFiveDollarModeDaily.account_id == user.account_id,
        TriviaUserFiveDollarModeDaily.date == target_date
    ).first()
    
    # Check if user is a winner for yesterday's draw
    yesterday_draw = target_date - date.resolution
    is_winner = db.query(TriviaFiveDollarModeWinners).filter(
        TriviaFiveDollarModeWinners.account_id == user.account_id,
        TriviaFiveDollarModeWinners.draw_date == yesterday_draw
    ).first() is not None
    
    return {
        'has_access': access_check['has_access'],
        'subscription_status': access_check['subscription_status'],
        'has_submitted': user_attempt is not None and user_attempt.submitted_at is not None,
        'submitted_at': user_attempt.submitted_at.isoformat() if user_attempt and user_attempt.submitted_at else None,
        'is_correct': user_attempt.is_correct if user_attempt else None,
        'is_winner': is_winner,
        'current_date': target_date.isoformat()
    }


@router.get("/leaderboard")
async def get_five_dollar_mode_leaderboard(
    draw_date: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get daily leaderboard for $5 mode.
    Shows winners ranked by submission time (position) and money awarded.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Parse draw_date or use yesterday's draw
    if draw_date:
        try:
            target_date = date.fromisoformat(draw_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        target_date = get_active_draw_date() - date.resolution  # Yesterday's draw
    
    # Get leaderboard entries
    leaderboard_entries = db.query(TriviaFiveDollarModeLeaderboard).filter(
        TriviaFiveDollarModeLeaderboard.draw_date == target_date
    ).order_by(
        TriviaFiveDollarModeLeaderboard.position,
        TriviaFiveDollarModeLeaderboard.submitted_at
    ).all()
    
    # Get user details
    result = []
    for entry in leaderboard_entries:
        user_obj = db.query(User).filter(User.account_id == entry.account_id).first()
        if user_obj:
            result.append({
                'position': entry.position,
                'username': user_obj.username,
                'user_id': entry.account_id,
                'money_awarded': entry.money_awarded,
                'submitted_at': entry.submitted_at.isoformat() if entry.submitted_at else None
            })
    
    return {
        'draw_date': target_date.isoformat(),
        'leaderboard': result
    }

