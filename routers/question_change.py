from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
from models import User, Trivia, TriviaQuestionsDaily
from routers.dependencies import get_current_user
from db import get_db

router = APIRouter(prefix="/question-change", tags=["Question Change"])

@router.post("/change-question")
async def change_question(
    current_question_number: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Change the current question to a new one.
    User must have question change boost available.
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    today = datetime.utcnow().date()
    
    # Check if user has already answered correctly today
    already_correct = db.query(TriviaQuestionsDaily).filter(
        TriviaQuestionsDaily.account_id == user.account_id,
        func.date(TriviaQuestionsDaily.date) == today,
        TriviaQuestionsDaily.is_correct == True
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
        TriviaQuestionsDaily.is_used == False
    ).first()
    
    if not current_daily_question:
        raise HTTPException(status_code=400, detail="Question not found or already used")
    
    # Check if user has question change boost (implement your boost logic here)
    # For now, we'll allow one change per day
    if current_daily_question.was_changed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="You have already changed this question today."
        )
    
    # Find a new unused question
    new_question = db.query(Trivia).filter(
        Trivia.question_done == False
    ).order_by(func.random()).first()
    
    if not new_question:
        raise HTTPException(status_code=400, detail="No questions available")
    
    # Update daily question
    current_daily_question.question_number = new_question.question_number
    current_daily_question.was_changed = True
    
    # Mark new question as used
    new_question.question_done = True
    new_question.que_displayed_date = datetime.utcnow()
    
    db.commit()
    
    return {
        "success": True,
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
            "picture_url": new_question.picture_url
        }
    }
