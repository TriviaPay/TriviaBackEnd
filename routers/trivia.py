from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from typing import List, Optional
from datetime import datetime, timedelta, date
import random
import json

from db import get_db
from models import User, Trivia, TriviaQuestionsDaily, TriviaQuestionsEntries, TriviaUserDaily, UserDailyRewards
from routers.dependencies import get_current_user
from pathlib import Path as FilePath

router = APIRouter(prefix="/trivia", tags=["Trivia"])

# Load store configuration for boost costs
STORE_CONFIG_PATH = FilePath("config/store_items.json")
with open(STORE_CONFIG_PATH) as f:
    store_config = json.load(f)

@router.get("/questions")
async def get_daily_questions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    check_expiration: bool = None,
    require_email: bool = None
):
    """
    Get today's shared question pool (0-4 questions) with user's unlock status.
    Returns all questions in today's pool with unlock status per user.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    today = date.today()
    
    # Get today's shared pool (0-4 questions)
    daily_pool = db.query(TriviaQuestionsDaily).filter(
        func.date(TriviaQuestionsDaily.date) == today
    ).order_by(TriviaQuestionsDaily.question_order).all()
    
    # Get user's unlocks for today
    user_unlocks = {
        (ud.date, ud.question_order): ud 
        for ud in db.query(TriviaUserDaily).filter(
            TriviaUserDaily.account_id == user.account_id,
            TriviaUserDaily.date == today
        ).all()
    }
    
    questions = []
    for dq in daily_pool:
        user_daily = user_unlocks.get((today, dq.question_order))
        q = dq.question
        
        unlock_status = {
            "is_unlocked": user_daily is not None and user_daily.unlock_method is not None,
            "unlock_method": user_daily.unlock_method if user_daily else None,
            "status": user_daily.status if user_daily else "locked",
            "user_answer": user_daily.user_answer if user_daily else None,
            "is_correct": user_daily.is_correct if user_daily else None,
            "answered_at": user_daily.answered_at.isoformat() if user_daily and user_daily.answered_at else None,
        }
        
        questions.append({
            "question_number": q.question_number,
            "question": q.question,
            "options": {
                "a": q.option_a,
                "b": q.option_b,
                "c": q.option_c,
                "d": q.option_d
            },
            "category": q.category,
            "difficulty": q.difficulty_level,
            "picture_url": q.picture_url,
            "order": dq.question_order,
            "is_common": dq.is_common,
            **unlock_status
        })

    return {"questions": questions}

@router.get("/current-question")
async def get_current_question(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get the user's current question based on their unlocks.
    Auto-unlocks Q1 if eligible (free, first question).
    Returns the question the user should see next.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    today = date.today()
    
    # Get today's shared pool
    daily_pool = db.query(TriviaQuestionsDaily).filter(
        func.date(TriviaQuestionsDaily.date) == today
    ).order_by(TriviaQuestionsDaily.question_order).all()
    
    if not daily_pool:
        raise HTTPException(status_code=404, detail="No questions available for today")
    
    # Check if user has answered correctly today
    user_correct = db.query(TriviaUserDaily).filter(
        TriviaUserDaily.account_id == user.account_id,
        TriviaUserDaily.date == today,
        TriviaUserDaily.status == 'answered_correct'
    ).first()
    
    if user_correct:
        # User already answered correctly, return that question
        dq = db.query(TriviaQuestionsDaily).filter(
            func.date(TriviaQuestionsDaily.date) == today,
            TriviaQuestionsDaily.question_order == user_correct.question_order
        ).first()
        if not dq:
            raise HTTPException(status_code=404, detail="Question not found")
        
        q = dq.question
        return {
            "question_number": q.question_number,
            "question": q.question,
            "options": {
                "a": q.option_a,
                "b": q.option_b,
                "c": q.option_c,
                "d": q.option_d
            },
            "category": q.category,
            "difficulty": q.difficulty_level,
            "picture_url": q.picture_url,
            "hint": q.hint,
            "correct_answer": q.correct_answer,
            "total_gems": user.gems,
            "order": dq.question_order,
            "is_common": dq.is_common,
            "is_correct": user_correct.is_correct,
            "user_answer": user_correct.user_answer,
            "explanation": q.explanation,
            "answered_at": user_correct.answered_at.isoformat() if user_correct.answered_at else None,
            "daily_completed": True
        }
    
    # Get user's unlocks for today
    user_unlocks = db.query(TriviaUserDaily).filter(
        TriviaUserDaily.account_id == user.account_id,
        TriviaUserDaily.date == today
    ).order_by(TriviaUserDaily.question_order).all()
    
    # Determine next allowed question order
    max_unlocked_order = 0
    if user_unlocks:
        max_unlocked_order = max(ud.question_order for ud in user_unlocks if ud.unlock_method is not None)
    
    # Auto-unlock Q1 if not unlocked yet and it exists
    q1 = next((dq for dq in daily_pool if dq.question_order == 1), None)
    if q1 and max_unlocked_order == 0:
        # Auto-unlock Q1 for free
        user_daily = TriviaUserDaily(
            account_id=user.account_id,
            date=today,
            question_order=1,
            question_number=q1.question_number,
            unlock_method='free',
            viewed_at=datetime.utcnow(),
            status='viewed'
        )
        db.add(user_daily)
        # Mark daily pool question as used
        q1.is_used = True
        db.commit()
        db.refresh(user_daily)
        max_unlocked_order = 1
    
    # Find current question (highest unlocked, not yet answered correctly)
    current_dq = None
    for order in range(max_unlocked_order, 0, -1):
        user_daily = next((ud for ud in user_unlocks if ud.question_order == order), None)
        if user_daily and user_daily.status not in ['answered_correct', 'skipped']:
            current_dq = next((dq for dq in daily_pool if dq.question_order == order), None)
            break
    
    if not current_dq:
        # Get Q1 if available
        current_dq = q1 or daily_pool[0]
        if current_dq.question_order == 1 and max_unlocked_order == 0:
            # Should have been unlocked above, but just in case
            user_daily = TriviaUserDaily(
                account_id=user.account_id,
                date=today,
                question_order=1,
                question_number=current_dq.question_number,
                unlock_method='free',
                viewed_at=datetime.utcnow(),
                status='viewed'
            )
            db.add(user_daily)
            current_dq.is_used = True
            db.commit()
    
    q = current_dq.question
    current_user_daily = next((ud for ud in user_unlocks if ud.question_order == current_dq.question_order), None)
    
    return {
        "question_number": q.question_number,
        "question": q.question,
        "options": {
            "a": q.option_a,
            "b": q.option_b,
            "c": q.option_c,
            "d": q.option_d
        },
        "category": q.category,
        "difficulty": q.difficulty_level,
        "picture_url": q.picture_url,
        "hint": q.hint,
        "correct_answer": q.correct_answer,
        "total_gems": user.gems,
        "order": current_dq.question_order,
        "is_common": current_dq.is_common,
        "status": current_user_daily.status if current_user_daily else "viewed",
        "total_questions": len(daily_pool),
        "daily_completed": False
    }

@router.post("/unlock-next")
async def unlock_next_question(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Unlock the next question in sequence (Q2, Q3, or Q4) using gems.
    Charges 10 gems (change_question cost from config).
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    today = date.today()
    
    # Check if user has answered correctly today
    user_correct = db.query(TriviaUserDaily).filter(
        TriviaUserDaily.account_id == user.account_id,
        TriviaUserDaily.date == today,
        TriviaUserDaily.status == 'answered_correct'
    ).first()
    
    if user_correct:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have already answered correctly today. Come back tomorrow!"
        )
    
    # Get user's unlocks to find next order
    user_unlocks = db.query(TriviaUserDaily).filter(
        TriviaUserDaily.account_id == user.account_id,
        TriviaUserDaily.date == today,
        TriviaUserDaily.unlock_method.isnot(None)
    ).order_by(TriviaUserDaily.question_order).all()
    
    max_unlocked_order = max((ud.question_order for ud in user_unlocks), default=0)
    next_order = max_unlocked_order + 1
    
    if next_order > 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All questions are already unlocked"
        )
    
    # Get next question from today's pool
    next_daily = db.query(TriviaQuestionsDaily).filter(
        func.date(TriviaQuestionsDaily.date) == today,
        TriviaQuestionsDaily.question_order == next_order
    ).first()
    
    if not next_daily:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Question {next_order} not available for today"
        )
    
    # Check cost
    boost_cost = store_config["gameplay_boosts"]["change_question"]["gems"]
    
    if user.gems < boost_cost:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient gems. You need {boost_cost} gems to unlock the next question."
        )
    
    # Charge gems
    user.gems -= boost_cost
    
    # Create or update user_daily record
    existing = db.query(TriviaUserDaily).filter(
        TriviaUserDaily.account_id == user.account_id,
        TriviaUserDaily.date == today,
        TriviaUserDaily.question_order == next_order
    ).first()
    
    if existing:
        existing.unlock_method = 'gems'
        existing.viewed_at = datetime.utcnow()
        existing.status = 'viewed'
        user_daily = existing
    else:
        user_daily = TriviaUserDaily(
            account_id=user.account_id,
            date=today,
            question_order=next_order,
            question_number=next_daily.question_number,
            unlock_method='gems',
            viewed_at=datetime.utcnow(),
            status='viewed'
        )
        db.add(user_daily)
    
    # Mark daily pool question as used
    next_daily.is_used = True
    
    db.commit()
    db.refresh(user_daily)
    
    q = next_daily.question
    
    return {
        "success": True,
        "remaining_gems": user.gems,
        "unlocked_question": {
            "question_number": q.question_number,
            "question": q.question,
            "options": {
                "a": q.option_a,
                "b": q.option_b,
                "c": q.option_c,
                "d": q.option_d
            },
            "category": q.category,
            "difficulty": q.difficulty_level,
            "picture_url": q.picture_url,
            "hint": q.hint,
            "correct_answer": q.correct_answer,
            "order": next_daily.question_order,
            "is_common": next_daily.is_common
        }
    }

@router.post("/retry/{question_number}")
async def retry_question(
    question_number: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Retry a question after answering incorrectly (150 gems).
    Resets the question to allow a new attempt.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    today = date.today()
    
    # Check if user has answered correctly today
    user_correct = db.query(TriviaUserDaily).filter(
        TriviaUserDaily.account_id == user.account_id,
        TriviaUserDaily.date == today,
        TriviaUserDaily.status == 'answered_correct'
    ).first()
    
    if user_correct:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have already answered correctly today. Come back tomorrow!"
        )
    
    # Find user's row for this question today
    user_daily = db.query(TriviaUserDaily).filter(
        TriviaUserDaily.account_id == user.account_id,
        TriviaUserDaily.date == today,
        TriviaUserDaily.question_number == question_number
    ).first()
    
    if not user_daily:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Question not found or not unlocked"
        )
    
    # Verify status is answered_wrong
    if user_daily.status != 'answered_wrong':
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot retry question. Current status: {user_daily.status}"
        )
    
    # Check cost
    boost_cost = store_config["gameplay_boosts"]["extra_chance"]["gems"]
    
    if user.gems < boost_cost:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient gems. You need {boost_cost} gems to retry this question."
        )
    
    # Charge gems
    user.gems -= boost_cost
    
    # Reset question for retry
    user_daily.status = 'viewed'
    user_daily.user_answer = None
    user_daily.is_correct = None
    user_daily.answered_at = None
    user_daily.retry_count += 1
    
    db.commit()
    db.refresh(user_daily)
    
    # Get question details
    question = db.query(Trivia).filter(Trivia.question_number == question_number).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Get daily pool info
    daily_q = db.query(TriviaQuestionsDaily).filter(
        func.date(TriviaQuestionsDaily.date) == today,
        TriviaQuestionsDaily.question_number == question_number
    ).first()
    
    return {
        "success": True,
        "remaining_gems": user.gems,
        "retry_count": user_daily.retry_count,
        "question": {
            "question_number": question.question_number,
            "question": question.question,
            "options": {
                "a": question.option_a,
                "b": question.option_b,
                "c": question.option_c,
                "d": question.option_d
            },
            "category": question.category,
            "difficulty": question.difficulty_level,
            "picture_url": question.picture_url,
            "hint": question.hint,
            "order": daily_q.question_order if daily_q else None,
            "is_common": daily_q.is_common if daily_q else False
        }
    }

@router.post("/submit-answer")
async def submit_answer(
    question_number: int,
    answer: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Submit answer for a question.
    Validates question is unlocked, sequential answering, and not already answered correctly today.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    today = date.today()
    
    # Check if user has already answered correctly today
    user_correct = db.query(TriviaUserDaily).filter(
        TriviaUserDaily.account_id == user.account_id,
        TriviaUserDaily.date == today,
        TriviaUserDaily.status == 'answered_correct'
    ).first()
    
    if user_correct:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have already answered correctly today. Come back tomorrow for new questions!"
        )
    
    # Find user's row for this question today
    user_daily = db.query(TriviaUserDaily).filter(
        TriviaUserDaily.account_id == user.account_id,
        TriviaUserDaily.date == today,
        TriviaUserDaily.question_number == question_number
    ).first()
    
    if not user_daily:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Question not found or not unlocked"
        )
    
    # Validate question is unlocked
    if user_daily.unlock_method is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question is not unlocked"
        )
    
    # Validate sequential answering
    user_unlocks = db.query(TriviaUserDaily).filter(
        TriviaUserDaily.account_id == user.account_id,
        TriviaUserDaily.date == today,
        TriviaUserDaily.unlock_method.isnot(None)
    ).order_by(TriviaUserDaily.question_order).all()
    
    # Check if user is answering in order
    answered_orders = [ud.question_order for ud in user_unlocks if ud.status in ['answered_correct', 'answered_wrong']]
    if answered_orders and max(answered_orders) >= user_daily.question_order:
        # Check if there's a gap (unanswered question before this one)
        expected_next = max(answered_orders) + 1
        if user_daily.question_order > expected_next:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Please answer questions in order. You should answer question {expected_next} next."
            )
    
    # Validate not already answered
    if user_daily.status in ['answered_correct', 'answered_wrong']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question already answered"
        )
    
    # Get the question
    question = db.query(Trivia).filter(Trivia.question_number == question_number).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    # Check answer
    is_correct = answer.lower() == question.correct_answer.lower()
    
    # Update user_daily
    user_daily.user_answer = answer
    user_daily.is_correct = is_correct
    user_daily.answered_at = datetime.utcnow()
    user_daily.status = 'answered_correct' if is_correct else 'answered_wrong'
    
    # Update or create entries record
    entry = db.query(TriviaQuestionsEntries).filter(
        TriviaQuestionsEntries.account_id == user.account_id,
        TriviaQuestionsEntries.date == today
    ).first()
    
    if not entry:
        entry = TriviaQuestionsEntries(
            account_id=user.account_id,
            ques_attempted=1,
            correct_answers=1 if is_correct else 0,
            wrong_answers=0 if is_correct else 1,
            date=today
        )
        db.add(entry)
    else:
        entry.ques_attempted += 1
        if is_correct:
            entry.correct_answers += 1
        else:
            entry.wrong_answers += 1
    
    # If correct, mark remaining questions as skipped
    if is_correct:
        from rewards_logic import update_user_eligibility
        update_user_eligibility(db, user.account_id, today)
        
        # Mark all remaining unlocked questions as skipped
        remaining = db.query(TriviaUserDaily).filter(
            TriviaUserDaily.account_id == user.account_id,
            TriviaUserDaily.date == today,
            TriviaUserDaily.question_order > user_daily.question_order,
            TriviaUserDaily.status.notin_(['answered_correct', 'answered_wrong'])
        ).all()
        
        for rem in remaining:
            rem.status = 'skipped'
    
    db.commit()

    return {
        "is_correct": is_correct,
        "correct_answer": question.correct_answer,
        "explanation": question.explanation,
        "daily_completed": is_correct
    }

@router.get("/")
def get_trivia_questions(db: Session = Depends(get_db), all: bool = False):
    """
    Endpoint to fetch trivia questions.
    Fetches active trivia questions from the database.
    Set 'all=true' to retrieve all questions including used ones.
    """
    if all:
        questions = db.query(Trivia).all()
    else:
        questions = db.query(Trivia).filter(Trivia.question_done == False).all()
    
    return {
        "questions": [
            {
                "question_number": q.question_number,
                "question": q.question,
                "options": [q.option_a, q.option_b, q.option_c, q.option_d],
                "category": q.category,
                "difficulty_level": q.difficulty_level,
            }
            for q in questions
        ]
    }

@router.get("/countries")
def get_countries(db: Session = Depends(get_db)):
    """
    Fetch distinct countries from the trivia table.
    """
    countries = db.query(Trivia.country).distinct().all()
    return {"countries": [c[0] for c in countries if c[0] is not None]}

@router.get("/categories")
def get_categories(db: Session = Depends(get_db)):
    """
    Fetch distinct categories from the trivia table.
    """
    categories = db.query(Trivia.category).distinct().all()
    return {"categories": [c[0] for c in categories if c[0] is not None]}

@router.get("/daily-login")
async def get_daily_login_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current week's daily login status"""
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    today = date.today()
    # Calculate week start (Monday)
    week_start = today - timedelta(days=today.weekday())
    
    # Get or create user's weekly rewards record
    user_rewards = db.query(UserDailyRewards).filter(
        UserDailyRewards.account_id == user.account_id,
        UserDailyRewards.week_start_date == week_start
    ).first()
    
    if not user_rewards:
        # No record means no days claimed yet this week
        days_claimed = []
        total_gems_earned = 0
    else:
        # Build list of claimed days (1-7 for Mon-Sun)
        days_claimed = []
        if user_rewards.day1_status: days_claimed.append(1)
        if user_rewards.day2_status: days_claimed.append(2)
        if user_rewards.day3_status: days_claimed.append(3)
        if user_rewards.day4_status: days_claimed.append(4)
        if user_rewards.day5_status: days_claimed.append(5)
        if user_rewards.day6_status: days_claimed.append(6)
        if user_rewards.day7_status: days_claimed.append(7)
        
        # Calculate total gems earned (10 per day, 30 for Sunday)
        total_gems_earned = len([d for d in days_claimed if d != 7]) * 10
        if 7 in days_claimed:
            total_gems_earned += 30
    
    # Current day of week (0=Monday, 6=Sunday, convert to 1-7)
    current_day = today.weekday() + 1
    
    # Days remaining in week
    days_remaining = 7 - len(days_claimed)
    
    return {
        "week_start_date": week_start.isoformat(),
        "current_day": current_day,
        "days_claimed": days_claimed,
        "days_remaining": days_remaining,
        "total_gems_earned_this_week": total_gems_earned,
        "day_status": {
            "monday": user_rewards.day1_status if user_rewards else False,
            "tuesday": user_rewards.day2_status if user_rewards else False,
            "wednesday": user_rewards.day3_status if user_rewards else False,
            "thursday": user_rewards.day4_status if user_rewards else False,
            "friday": user_rewards.day5_status if user_rewards else False,
            "saturday": user_rewards.day6_status if user_rewards else False,
            "sunday": user_rewards.day7_status if user_rewards else False,
        }
    }

@router.post("/daily-login")
async def process_daily_login(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Process daily login rewards - weekly calendar system"""
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    today = date.today()
    # Calculate week start (Monday)
    week_start = today - timedelta(days=today.weekday())
    
    # Get or create user's weekly rewards record
    user_rewards = db.query(UserDailyRewards).filter(
        UserDailyRewards.account_id == user.account_id,
        UserDailyRewards.week_start_date == week_start
    ).first()
    
    if not user_rewards:
        user_rewards = UserDailyRewards(
            account_id=user.account_id,
            week_start_date=week_start
        )
        db.add(user_rewards)
    
    # Determine which day of week (1=Monday, 7=Sunday)
    day_of_week = today.weekday() + 1
    
    # Check if already claimed today
    day_status_map = {
        1: user_rewards.day1_status,
        2: user_rewards.day2_status,
        3: user_rewards.day3_status,
        4: user_rewards.day4_status,
        5: user_rewards.day5_status,
        6: user_rewards.day6_status,
        7: user_rewards.day7_status,
    }
    
    if day_status_map[day_of_week]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Daily reward already claimed today"
        )
    
    # Award gems: 10 for Mon-Sat, 30 for Sunday
    gems_earned = 30 if day_of_week == 7 else 10
    user.gems += gems_earned
    
    # Mark the day as claimed
    if day_of_week == 1:
        user_rewards.day1_status = True
    elif day_of_week == 2:
        user_rewards.day2_status = True
    elif day_of_week == 3:
        user_rewards.day3_status = True
    elif day_of_week == 4:
        user_rewards.day4_status = True
    elif day_of_week == 5:
        user_rewards.day5_status = True
    elif day_of_week == 6:
        user_rewards.day6_status = True
    elif day_of_week == 7:
        user_rewards.day7_status = True
    
    user_rewards.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user_rewards)
    
    # Calculate days claimed for response
    days_claimed = []
    if user_rewards.day1_status: days_claimed.append(1)
    if user_rewards.day2_status: days_claimed.append(2)
    if user_rewards.day3_status: days_claimed.append(3)
    if user_rewards.day4_status: days_claimed.append(4)
    if user_rewards.day5_status: days_claimed.append(5)
    if user_rewards.day6_status: days_claimed.append(6)
    if user_rewards.day7_status: days_claimed.append(7)
    
    return {
        "success": True,
        "gems_earned": gems_earned,
        "total_gems": user.gems,
        "week_start_date": week_start.isoformat(),
        "current_day": day_of_week,
        "days_claimed": days_claimed,
        "days_remaining": 7 - len(days_claimed)
    }

@router.get("/question-status/{question_number}")
async def get_question_status(
    question_number: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get the status of a specific question for the current user"""
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    today = date.today()
    
    user_daily = db.query(TriviaUserDaily).filter(
        TriviaUserDaily.account_id == user.account_id,
        TriviaUserDaily.question_number == question_number,
        TriviaUserDaily.date == today
    ).first()
    
    if not user_daily:
        raise HTTPException(status_code=404, detail="Question not found or not unlocked")
    
    question = db.query(Trivia).filter(Trivia.question_number == question_number).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    return {
        "question_number": question_number,
        "is_answered": user_daily.status in ['answered_correct', 'answered_wrong'],
        "is_correct": user_daily.is_correct,
        "user_answer": user_daily.user_answer,
        "correct_answer": question.correct_answer if user_daily.status in ['answered_correct', 'answered_wrong'] else None,
        "answered_at": user_daily.answered_at.isoformat() if user_daily.answered_at else None,
        "explanation": question.explanation if user_daily.status in ['answered_correct', 'answered_wrong'] else None,
        "status": user_daily.status
    }

@router.post("/reset-questions")
async def reset_questions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Reset all trivia questions to unused status (for admin use)"""
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    updated_count = db.query(Trivia).update({Trivia.question_done: False})
    db.commit()
    
    return {
        "message": f"Successfully reset {updated_count} questions to unused status",
        "updated_count": updated_count
    }

@router.get("/boost-availability")
async def get_boost_availability(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get the number of each boost the user can afford with their current gems.
    Shows how many of each boost type can be purchased.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    gameplay_boosts = store_config.get("gameplay_boosts", {})
    
    available_boosts = {}
    for boost_type, boost_info in gameplay_boosts.items():
        if 'gems' in boost_info:
            cost = boost_info['gems']
            if cost > 0:
                available_count = user.gems // cost
                available_boosts[boost_type] = available_count
            else:
                available_boosts[boost_type] = 0
        else:
            available_boosts[boost_type] = 0
    
    return {
        "available_boosts": available_boosts,
        "total_gems": user.gems,
        "boost_costs": {boost_type: boost_info.get('gems', 0) 
                       for boost_type, boost_info in gameplay_boosts.items() 
                       if 'gems' in boost_info}
    }
