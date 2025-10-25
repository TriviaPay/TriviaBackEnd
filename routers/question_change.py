from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
from models import User, Trivia, TriviaQuestionsDaily
from routers.dependencies import get_current_user
from db import get_db
import json
from pathlib import Path as FilePath

router = APIRouter(prefix="/question-change", tags=["Question Change"])

# Load store configuration for boost costs
STORE_CONFIG_PATH = FilePath("config/store_items.json")
with open(STORE_CONFIG_PATH) as f:
    store_config = json.load(f)

@router.post("/change-question")
async def change_question(
    current_question_number: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Change the current question to a new one using the change_question boost.
    
    **Requirements:**
    - User must have at least 10 gems (cost from config)
    - User must have remaining question changes (max 3 per day)
    - Question must not have been attempted yet
    - User must not have answered correctly today
    
    **What happens:**
    - Deducts 10 gems from user's balance
    - Replaces current question with a new unused question
    - Marks the question as changed (counts toward daily limit)
    - Returns the new question with all details including hint and correct answer
    
    **Response includes:**
    - New question details (question, options, hint, correct_answer, etc.)
    - Remaining gems after purchase
    - Remaining question changes for the day
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    today = datetime.utcnow().date()
    
    # Check if user has already answered correctly today
    already_correct = db.query(TriviaQuestionsDaily).filter(
        TriviaQuestionsDaily.account_id == user.account_id,
        func.date(TriviaQuestionsDaily.date) == today,
        TriviaQuestionsDaily.user_is_correct == True
    ).first()
    
    if already_correct:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="You have already answered correctly today. Cannot change questions."
        )
    
    # Get current daily question
    current_daily_question = db.query(TriviaQuestionsDaily).filter(
        TriviaQuestionsDaily.account_id == user.account_id,
        TriviaQuestionsDaily.question_number == current_question_number,
        func.date(TriviaQuestionsDaily.date) == today,
        TriviaQuestionsDaily.user_attempted == False
    ).first()
    
    if not current_daily_question:
        raise HTTPException(status_code=400, detail="Question not found or already attempted")
    
    # Check remaining question changes for today
    daily_questions = db.query(TriviaQuestionsDaily).filter(
        TriviaQuestionsDaily.account_id == user.account_id,
        func.date(TriviaQuestionsDaily.date) == today
    ).all()
    
    changes_used_today = sum(1 for dq in daily_questions if dq.was_changed)
    if changes_used_today >= 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="You have used all 3 question changes for today."
        )
    
    # Check if this specific question was already changed
    if current_daily_question.was_changed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="This question has already been changed today."
        )
    
    # Get boost cost from config
    boost_cost = store_config["gameplay_boosts"]["change_question"]["gems"]
    
    # Check if user has enough gems
    if user.gems < boost_cost:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient gems. You need {boost_cost} gems to change question."
        )
    
    # Find a new unused question
    new_question = db.query(Trivia).filter(
        Trivia.question_done == False
    ).order_by(func.random()).first()
    
    if not new_question:
        raise HTTPException(status_code=400, detail="No questions available")
    
    # Deduct gems
    user.gems -= boost_cost
    
    # Update daily question
    current_daily_question.question_number = new_question.question_number
    current_daily_question.was_changed = True
    
    # Mark new question as used
    new_question.question_done = True
    new_question.que_displayed_date = datetime.utcnow()
    
    db.commit()
    
    return {
        "success": True,
        "remaining_gems": user.gems,
        "changes_remaining": 3 - (changes_used_today + 1),
        "new_question": {
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
            "hint": new_question.hint,
            "correct_answer": new_question.correct_answer
        }
    }
