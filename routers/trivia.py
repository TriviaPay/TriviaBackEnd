from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime, timedelta
import random
import json

from db import get_db
from models import User, Trivia, DailyQuestion, Entry
from auth import verify_access_token
from routers.dependencies import get_current_user

router = APIRouter(prefix="/trivia", tags=["Trivia"])

@router.get("/questions")
async def get_daily_questions(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all of user's daily questions (deprecated, use /current-question instead)"""
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

@router.get("/current-question")
async def get_current_question(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get the user's current question (either the common question or the next unanswered one)"""
    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get today's questions
    today = datetime.utcnow().date()
    
    # First, check if the user has already answered a question correctly today
    daily_questions = db.query(DailyQuestion).filter(
        DailyQuestion.account_id == user.account_id,
        func.date(DailyQuestion.date) == today
    ).order_by(DailyQuestion.question_order).all()
    
    # Check if user has already answered a question correctly today
    correct_question = None
    for dq in daily_questions:
        if dq.is_used and dq.is_correct:
            correct_question = dq
            break
    
    # If user has answered correctly today, return that question
    if correct_question:
        q = correct_question.question
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
            "order": correct_question.question_order,
            "is_common": correct_question.is_common,
            "is_used": correct_question.is_used,
            "total_questions": len(daily_questions),
            "questions_answered": sum(1 for dq in daily_questions if dq.is_used),
            "is_correct": correct_question.is_correct,
            "correct_answer": q.correct_answer,
            "user_answer": correct_question.answer,
            "explanation": q.explanation,
            "answered_at": correct_question.answered_at,
            "daily_completed": True  # Indicate that daily trivia is completed
        }
    
    # If no questions allocated today or no correct answers yet, proceed with normal logic
    if not daily_questions:
        # Get unused questions
        unused_questions = db.query(Trivia).filter(
            Trivia.question_done == False
        ).order_by(func.random()).limit(4).all()

        if len(unused_questions) < 4:
            raise HTTPException(status_code=400, detail="Not enough questions available")

        # Make sure the common question is the same for all users today
        # First, check if any user has already been assigned a common question today
        common_question = db.query(DailyQuestion).filter(
            func.date(DailyQuestion.date) == today,
            DailyQuestion.is_common == True
        ).first()
        
        if common_question:
            # Use the existing common question
            common_question_number = common_question.question_number
            # Replace the first question with the common one
            unused_questions[0] = db.query(Trivia).filter(
                Trivia.question_number == common_question_number
            ).first()
            
            # If we can't find the common question, just use the first random one
            if not unused_questions[0]:
                unused_questions[0] = db.query(Trivia).filter(
                    Trivia.question_done == False
                ).order_by(func.random()).first()

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

    # Find the current unanswered question
    current_question = None
    for dq in daily_questions:
        if not dq.is_used:
            current_question = dq
            break
    
    # If all questions are answered, return the last one
    if not current_question and daily_questions:
        current_question = daily_questions[-1]
        
    # If somehow we still don't have a question, return an error
    if not current_question:
        raise HTTPException(status_code=404, detail="No questions available")
    
    # Format response for the single question
    q = current_question.question
    
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
        "order": current_question.question_order,
        "is_common": current_question.is_common,
        "is_used": current_question.is_used,
        "total_questions": len(daily_questions),
        "questions_answered": sum(1 for dq in daily_questions if dq.is_used),
        "daily_completed": False  # Indicate that daily trivia is not completed yet
    }

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

    # Check if user has already answered a question correctly today
    today = datetime.utcnow().date()
    already_correct = db.query(DailyQuestion).filter(
        DailyQuestion.account_id == user.account_id,
        func.date(DailyQuestion.date) == today,
        DailyQuestion.is_used == True,
        DailyQuestion.is_correct == True
    ).first()
    
    if already_correct:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="You have already answered a question correctly today. Come back tomorrow for new questions!"
        )

    # Get the question
    question = db.query(Trivia).filter(Trivia.question_number == question_number).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    # Get daily question allocation
    daily_question = db.query(DailyQuestion).filter(
        DailyQuestion.account_id == user.account_id,
        DailyQuestion.question_number == question_number,
        func.date(DailyQuestion.date) == today
    ).first()

    if not daily_question:
        raise HTTPException(status_code=400, detail="Question not allocated for today")

    if daily_question.is_used:
        raise HTTPException(status_code=400, detail="Question already attempted")

    # Check answer
    is_correct = answer.lower() == question.correct_answer.lower()
    
    # Mark question as used and record the answer details
    daily_question.is_used = True
    daily_question.answer = answer
    daily_question.is_correct = is_correct
    daily_question.answered_at = datetime.utcnow()
    
    # Update the Entry table for reward eligibility
    entry = db.query(Entry).filter(
        Entry.account_id == user.account_id,
        Entry.date == today
    ).first()
    
    if not entry:
        # Create a new entry if one doesn't exist for today
        entry = Entry(
            account_id=user.account_id,
            number_of_entries=0,
            ques_attempted=1,
            correct_answers=1 if is_correct else 0,
            wrong_answers=0 if is_correct else 1,
            date=today
        )
        db.add(entry)
    else:
        # Update existing entry
        entry.ques_attempted += 1
        if is_correct:
            entry.correct_answers += 1
        else:
            entry.wrong_answers += 1
    
    db.commit()
    
    # If answer is correct, mark all remaining questions as used to prevent further attempts
    if is_correct:
        remaining_questions = db.query(DailyQuestion).filter(
            DailyQuestion.account_id == user.account_id,
            func.date(DailyQuestion.date) == today,
            DailyQuestion.is_used == False
        ).all()
        
        for q in remaining_questions:
            q.is_used = True
            q.answer = None
            q.is_correct = None
        
        db.commit()

    return {
        "is_correct": is_correct,
        "correct_answer": question.correct_answer,
        "explanation": question.explanation,
        "daily_completed": is_correct  # Indicate if daily trivia is completed
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

@router.get("/question-status/{question_number}")
async def get_question_status(
    question_number: int,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get the status of a specific question for the current user (whether it's answered and if correct)"""
    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get today's daily question allocation for this question
    today = datetime.utcnow().date()
    daily_question = db.query(DailyQuestion).filter(
        DailyQuestion.account_id == user.account_id,
        DailyQuestion.question_number == question_number,
        func.date(DailyQuestion.date) == today
    ).first()
    
    if not daily_question:
        raise HTTPException(status_code=404, detail="Question not found in today's allocation")
    
    # Get the question details
    question = db.query(Trivia).filter(Trivia.question_number == question_number).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Return the status
    return {
        "question_number": question_number,
        "is_answered": daily_question.is_used,
        "is_correct": daily_question.is_correct,
        "user_answer": daily_question.answer,
        "correct_answer": question.correct_answer if daily_question.is_used else None,
        "answered_at": daily_question.answered_at,
        "explanation": question.explanation if daily_question.is_used else None
    }

