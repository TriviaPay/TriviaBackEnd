from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime, timedelta
import random

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
    
    # Update user stats
    if is_correct:
        # Update streak
        if user.last_streak_date and user.last_streak_date.date() == (today - timedelta(days=1)):
            user.streaks += 1
        else:
            user.streaks = 1
        user.last_streak_date = datetime.utcnow()
        
        # Add gems for correct answer
        user.gems += 10
    else:
        # Reset streak on wrong answer
        user.streaks = 0
        
    db.commit()

    return {
        "is_correct": is_correct,
        "correct_answer": question.correct_answer,
        "explanation": question.explanation,
        "gems": user.gems,
        "streaks": user.streaks
    }

@router.post("/lifeline")
async def use_lifeline(
    question_number: int,
    lifeline_type: str,  # "fifty-fifty", "change", "hint"
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Use a lifeline on a question"""
    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user has enough gems
    if user.gems < 5:
        raise HTTPException(status_code=400, detail="Not enough gems")

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

    # Process lifeline
    response = {}
    if lifeline_type == "fifty-fifty":
        # Get correct answer and one random wrong answer
        options = ["a", "b", "c", "d"]
        # Find which option letter corresponds to the correct answer
        correct_option = None
        for opt in options:
            if getattr(question, f"option_{opt}").lower() == question.correct_answer.lower():
                correct_option = opt
                break
        
        if not correct_option:
            raise HTTPException(status_code=500, detail="Could not find correct option")
            
        wrong_options = [opt for opt in options if opt != correct_option]
        random_wrong = random.choice(wrong_options)
        
        response = {
            "options": {
                correct_option: getattr(question, f"option_{correct_option}"),
                random_wrong: getattr(question, f"option_{random_wrong}")
            }
        }

    elif lifeline_type == "change":
        if user.lifeline_changes_remaining <= 0:
            raise HTTPException(status_code=400, detail="No question changes remaining")

        # Get a new unused question
        new_question = db.query(Trivia).filter(
            Trivia.question_done == False
        ).order_by(func.random()).first()

        if not new_question:
            raise HTTPException(status_code=400, detail="No more questions available")

        # Update daily question
        daily_question.question_number = new_question.question_number
        daily_question.was_changed = True
        
        # Mark new question as used
        new_question.question_done = True
        new_question.que_displayed_date = datetime.utcnow()
        
        # Decrease remaining changes
        user.lifeline_changes_remaining -= 1

        response = {
            "question_number": new_question.question_number,
            "question": new_question.question,
            "options": {
                "a": new_question.option_a,
                "b": new_question.option_b,
                "c": new_question.option_c,
                "d": new_question.option_d
            },
            "category": new_question.category,
            "difficulty": new_question.difficulty_level,
            "picture_url": new_question.picture_url,
            "changes_remaining": user.lifeline_changes_remaining
        }

    elif lifeline_type == "hint":
        response = {
            "hint": question.explanation
        }

    else:
        raise HTTPException(status_code=400, detail="Invalid lifeline type")

    # Deduct gems
    user.gems -= 5
    db.commit()

    response["gems_remaining"] = user.gems
    return response

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

