from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime, timedelta
import random
import json

from db import get_db
from models import User, Trivia, DailyQuestion
from auth import verify_access_token
from routers.dependencies import get_current_user

router = APIRouter(prefix="/trivia", tags=["Trivia"])

@router.get("/questions")
async def get_daily_questions(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user's daily questions"""
    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get today's questions
    today = datetime.utcnow().date()
    daily_questions = db.query(DailyQuestion).filter(
        DailyQuestion.account_id == user.account_id,
        func.date(DailyQuestion.date) == today
    ).order_by(DailyQuestion.question_order).all()

    # If no questions allocated today, allocate new ones
    if not daily_questions:
        # Get unused questions
        unused_questions = db.query(Trivia).filter(
            Trivia.question_done == False
        ).order_by(func.random()).limit(4).all()

        if len(unused_questions) < 4:
            raise HTTPException(status_code=400, detail="Not enough questions available")

        # Allocate questions
        daily_questions = []
        for i, q in enumerate(unused_questions):
            dq = DailyQuestion(
                account_id=user.account_id,
                question_number=q.question_number,
                is_common=(i == 0),  # First question is common
                question_order=i + 1
            )
            db.add(dq)
            daily_questions.append(dq)
            
            # Mark question as used
            q.question_done = True
            q.que_displayed_date = datetime.utcnow()

        db.commit()

    # Format response
    questions = []
    for dq in daily_questions:
        q = dq.question
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
            "is_used": dq.is_used
        })

    return {"questions": questions}

@router.post("/submit-answer")
async def submit_answer(
    question_number: int,
    answer: str,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Submit answer for a question"""
    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get the question
    question = db.query(Trivia).filter(Trivia.question_number == question_number).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    # Get daily question allocation
    today = datetime.utcnow().date()
    daily_question = db.query(DailyQuestion).filter(
        DailyQuestion.account_id == user.account_id,
        DailyQuestion.question_number == question_number,
        func.date(DailyQuestion.date) == today
    ).first()

    if not daily_question:
        raise HTTPException(status_code=400, detail="Question not allocated for today")

    if daily_question.is_used:
        raise HTTPException(status_code=400, detail="Question already attempted")

    # Mark question as used
    daily_question.is_used = True
    db.commit()

    # Check answer
    is_correct = answer.lower() == question.correct_answer.lower()
    
    # No longer update streaks/gems here since they're for daily logins
    db.commit()

    return {
        "is_correct": is_correct,
        "correct_answer": question.correct_answer,
        "explanation": question.explanation
    }

@router.get("/")
def get_trivia_questions(db: Session = Depends(get_db)):
    """
    Endpoint to fetch trivia questions.
    Fetches active trivia questions from the database.
    """
    questions = db.query(Trivia).filter(Trivia.question_done == "False").all()
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

@router.post("/daily-login")
async def process_daily_login(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Process daily login rewards and streak bonuses"""
    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    today = datetime.utcnow().date()
    
    # Check if already claimed today
    if user.last_streak_date and user.last_streak_date.date() == today:
        raise HTTPException(status_code=400, detail="Daily reward already claimed today")

    # Calculate streak
    if user.last_streak_date and user.last_streak_date.date() == (today - timedelta(days=1)):
        # Consecutive day
        user.streaks += 1
    else:
        # Streak broken or first login
        user.streaks = 1

    # Add daily login bonus (10 gems)
    user.gems += 10

    # Check for weekly streak bonus (30 gems)
    if user.streaks % 7 == 0:  # Every 7 days
        user.gems += 30

    # Update last streak date
    user.last_streak_date = datetime.utcnow()
    db.commit()

    return {
        "gems_earned": 10 + (30 if user.streaks % 7 == 0 else 0),
        "total_gems": user.gems,
        "current_streak": user.streaks,
        "days_until_weekly_bonus": 7 - (user.streaks % 7)
    }

