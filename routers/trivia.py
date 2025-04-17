from fastapi import APIRouter, Depends, HTTPException, status, Body
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime, timedelta
import random
import json
import logging
import os
from pathlib import Path

from db import get_db
from models import User, Trivia, DailyQuestion, Entry, UserQuestionAnswer, Frame, UserFrame, Avatar, UserAvatar
from auth import verify_access_token
from routers.dependencies import get_current_user
from pydantic import BaseModel

router = APIRouter(prefix="/trivia", tags=["Trivia"])

# Load store configuration
STORE_CONFIG_PATH = Path("config/store_items.json")
with open(STORE_CONFIG_PATH) as f:
    store_config = json.load(f)

# Helper function to process boost usage
def process_boost_usage(user, boost_type, payment_type, db, logger):
    """
    Process the usage of a boost. Handles checking if user has boosts available
    and deducts either from boost count or gems/wallet based on payment type.
    
    Args:
        user: User model instance
        boost_type: String identifier for the boost type (e.g., "hint", "fifty_fifty")
        payment_type: Either "boost", "gems", or "usd"
        db: Database session
        logger: Logger instance
        
    Returns:
        dict with used_gems, used_usd and success keys
    """
    # Check boost mappings to user model attributes
    boost_count_mapping = {
        "hint": "hint_count",
        "fifty_fifty": "fifty_fifty_count",
        "question_reroll": "question_reroll_count",
        "auto_answer": "auto_answer_count",  # Direct mapping
        "auto_submit": "auto_answer_count"   # For backward compatibility
    }
    
    boost_used_today_mapping = {
        "hint": "hint_used_today",
        "fifty_fifty": "fifty_fifty_used_today",
        "auto_answer": "auto_answer_used_today",  # Direct mapping
        "auto_submit": "auto_answer_used_today"   # For backward compatibility
    }
    
    # Map back to store_config names
    store_config_mapping = {
        "auto_answer": "auto_submit",  # Map model attr to store_config key
    }
    
    store_boost_type = store_config_mapping.get(boost_type, boost_type)
    
    # Get the cost from store config
    if store_boost_type not in store_config["gameplay_boosts"]:
        logger.error(f"Boost type '{boost_type}' not found in store_config - mapped to '{store_boost_type}'")
        raise HTTPException(status_code=404, detail=f"Boost {boost_type} not found in store configuration")
    
    boost_config = store_config["gameplay_boosts"][store_boost_type]
    result = {
        "used_gems": 0,
        "used_usd": 0.0,
        "success": False
    }
    
    if payment_type == "boost":
        # Check if user has boost count available
        count_attr = boost_count_mapping.get(boost_type)
        used_today_attr = boost_used_today_mapping.get(boost_type)
        
        if not count_attr:
            raise HTTPException(status_code=400, detail=f"Invalid boost type: {boost_type}")
        
        has_boost = getattr(user, count_attr, 0) > 0
        used_today = False
        
        if used_today_attr:
            used_today = getattr(user, used_today_attr, False)
            
        if not has_boost:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"You don't have any {boost_type} boosts available."
            )
        
        if used_today and boost_type in boost_used_today_mapping:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"You've already used {boost_type} today."
            )
        
        # Use boost from existing count
        setattr(user, count_attr, getattr(user, count_attr) - 1)
        
        # Mark as used today if applicable
        if used_today_attr:
            setattr(user, used_today_attr, True)
            
        logger.info(f"User {user.account_id} used {boost_type} from count. Remaining: {getattr(user, count_attr)}")
        result["success"] = True
        
    elif payment_type == "gems":
        # Check if user has enough gems
        if "gems" not in boost_config:
            raise HTTPException(
                status_code=400, 
                detail=f"{boost_type} cannot be purchased with gems"
            )
        
        cost = boost_config["gems"]
        if user.gems < cost:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"You need {cost} gems to use {boost_type}."
            )
        
        # Deduct gems
        user.gems -= cost
        result["used_gems"] = cost
        logger.info(f"User {user.account_id} purchased {boost_type} for {cost} gems. Remaining gems: {user.gems}")
        result["success"] = True
        
    elif payment_type == "usd":
        # Check if user has enough balance
        if "usd" not in boost_config:
            raise HTTPException(
                status_code=400, 
                detail=f"{boost_type} cannot be purchased with USD"
            )
        
        cost = boost_config["usd"]
        if user.wallet_balance < cost:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"You need ${cost} to use {boost_type}."
            )
        
        # Deduct USD
        user.wallet_balance -= cost
        result["used_usd"] = cost
        logger.info(f"User {user.account_id} purchased {boost_type} for ${cost}. Remaining balance: {user.wallet_balance}")
        result["success"] = True
    
    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid payment type. Use 'boost', 'gems', or 'usd'."
        )
    
    return result

# --- Pydantic Models for API --- #
class BoostStatusResponse(BaseModel):
    streak_saver_count: int
    question_reroll_count: int
    extra_chance_count: int
    hint_count: int
    fifty_fifty_count: int
    auto_answer_count: int
    hint_available_today: bool
    fifty_fifty_available_today: bool
    auto_answer_available_today: bool

@router.get("/questions")
async def get_daily_questions(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all of user's daily questions (deprecated, use /current-question instead)"""
    logger = logging.getLogger(__name__)
    
    try:
        sub = claims.get("sub")
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
    
        # Get today's date
        today = datetime.utcnow().date()
        
        # Get daily questions for today
        daily_questions = db.query(DailyQuestion).filter(
            DailyQuestion.date == today
        ).order_by(DailyQuestion.question_order).all()
    
        # If no questions allocated today, allocate new ones
        if not daily_questions:
            # Get unused questions
            unused_questions = db.query(Trivia).filter(
                Trivia.question_done == False
            ).order_by(func.random()).limit(4).all()
    
            if len(unused_questions) < 4:
                # Not enough unused questions, reset some previous ones
                logger.warning("Not enough unused questions, resetting some previous ones")
                all_questions = db.query(Trivia).order_by(func.random()).limit(4).all()
                
                if len(all_questions) < 4:
                    raise HTTPException(status_code=400, detail="Not enough trivia questions in database")
                
                # Mark these as unused so we can use them
                for q in all_questions:
                    q.question_done = False
                
                unused_questions = all_questions
    
            # Allocate questions
            daily_questions = []
            for i, q in enumerate(unused_questions):
                dq = DailyQuestion(
                    question_number=q.question_number,
                    date=today,
                    is_common=(i == 0),  # First question is common
                    question_order=i + 1,
                    is_used=(i == 0),  # Common question (order 1) is always marked as used
                    correct_answer=q.correct_answer
                )
                db.add(dq)
                daily_questions.append(dq)
                
                # Mark question as used
                q.question_done = True
                q.que_displayed_date = datetime.utcnow()
    
            db.commit()
    
        # Check which questions the user has answered
        user_answers = db.query(UserQuestionAnswer).filter(
            UserQuestionAnswer.account_id == user.account_id,
            UserQuestionAnswer.date == today
        ).all()
        
        # Map of question_number to user answer
        answered_questions = {ua.question_number: ua for ua in user_answers}
    
        # Format response
        questions = []
        for dq in daily_questions:
            q = dq.question
            is_answered = dq.question_number in answered_questions
            
            question_data = {
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
            }
            
            # Add user's answer data if they've answered this question
            if is_answered:
                user_answer = answered_questions[dq.question_number]
                question_data.update({
                    "user_answer": user_answer.answer,
                    "is_correct": user_answer.is_correct,
                    "answered_at": user_answer.answered_at
                })
                
            questions.append(question_data)
    
        return {"questions": questions}
        
    except Exception as e:
        logger.error(f"Error in get_daily_questions: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

@router.get("/current-question")
async def get_current_question(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get the user's current question (either the common question or the next unanswered one)"""
    logger = logging.getLogger(__name__)
    
    try:
        sub = claims.get("sub")
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
    
        # Get today's date
        today = datetime.utcnow().date()
        
        # Get user's answers for today
        user_answers = db.query(UserQuestionAnswer).filter(
            UserQuestionAnswer.account_id == user.account_id,
            UserQuestionAnswer.date == today
        ).all()
        
        # Check if user has already answered a question correctly today
        correct_answer = None
        for ua in user_answers:
            if ua.is_correct:
                correct_answer = ua
                break
        
        # If user has answered correctly today, return that question with its details
        if correct_answer:
            # Get the daily question for this question number
            daily_question = db.query(DailyQuestion).filter(
                DailyQuestion.question_number == correct_answer.question_number,
                DailyQuestion.date == today
            ).first()
            
            if not daily_question:
                raise HTTPException(status_code=404, detail="Daily question not found")
                
            q = daily_question.question
            
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
                "order": daily_question.question_order,
                "is_common": daily_question.is_common,
                "is_used": daily_question.is_used,
                "total_questions": 4,  # Always 4 questions
                "questions_answered": len(user_answers),
                "is_correct": correct_answer.is_correct,
                "correct_answer": q.correct_answer,
                "user_answer": correct_answer.answer,
                "explanation": q.explanation,
                "answered_at": correct_answer.answered_at,
                "daily_completed": True  # Indicate that daily trivia is completed
            }
        
        # Get daily questions for today
        daily_questions = db.query(DailyQuestion).filter(
            DailyQuestion.date == today
        ).order_by(DailyQuestion.question_order).all()
        
        # If no questions allocated today, allocate new ones
        if not daily_questions:
            # Get unused questions
            unused_questions = db.query(Trivia).filter(
                Trivia.question_done == False
            ).order_by(func.random()).limit(4).all()
    
            if len(unused_questions) < 4:
                # Not enough unused questions, reset some previous ones
                logger.warning("Not enough unused questions, resetting some previous ones")
                all_questions = db.query(Trivia).order_by(func.random()).limit(4).all()
                
                if len(all_questions) < 4:
                    raise HTTPException(status_code=400, detail="Not enough trivia questions in database")
                
                # Mark these as unused so we can use them
                for q in all_questions:
                    q.question_done = False
                
                unused_questions = all_questions
    
            # Make sure the common question is the same for all users today
            # Since we've redesigned the table, the common question is automatically shared
    
            # Allocate questions
            daily_questions = []
            for i, q in enumerate(unused_questions):
                dq = DailyQuestion(
                    question_number=q.question_number,
                    date=today,
                    is_common=(i == 0),  # First question is common
                    question_order=i + 1,
                    is_used=(i == 0),  # Common question (order 1) is always marked as used
                    correct_answer=q.correct_answer
                )
                db.add(dq)
                daily_questions.append(dq)
                
                # Mark question as used in Trivia table
                q.question_done = True
                q.que_displayed_date = datetime.utcnow()
    
            db.commit()
    
        # Map of question_number to user answer
        answered_question_numbers = {ua.question_number for ua in user_answers}
        
        # Find the current unanswered question
        current_question = None
        
        # First, try to find the common question if not answered
        common_question = next((dq for dq in daily_questions if dq.is_common), None)
        if common_question and common_question.question_number not in answered_question_numbers:
            current_question = common_question
        
        # If common question answered, find the next question in order that's not answered
        if not current_question:
            # Sort by question_order to ensure we get the next one
            for dq in sorted(daily_questions, key=lambda x: x.question_order):
                if dq.question_number not in answered_question_numbers:
                    current_question = dq
                    break
        
        # If all questions are answered, return the common question
        if not current_question and daily_questions:
            current_question = common_question
            
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
            "questions_answered": len(user_answers),
            "daily_completed": False  # Indicate that daily trivia is not completed yet
        }
    except Exception as e:
        logger.error(f"Error in get_current_question: {str(e)}")
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

@router.post("/submit-answer")
async def submit_answer(
    question_number: int,
    answer: str,
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Submit answer for the single daily question.
       Accepts question_number and answer as query parameters.
    """
    # Ensure logger is available in function scope
    logger = logging.getLogger(__name__)
    
    # Log received parameters for debugging
    logger.info(f"submit_answer called with query params: question_number={question_number}, answer='{answer}'")

    # --- DEBUG: Log Entry model attributes --- #
    try:
        logger.debug(f"Entry model columns according to SQLAlchemy: {Entry.__table__.columns.keys()}")
    except Exception as log_err:
        logger.error(f"Error logging Entry model columns: {log_err}")
    # --- END DEBUG --- #

    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get today's date
    today = datetime.utcnow().date()
    
    # Check if user has already answered a question correctly today
    already_correct = db.query(UserQuestionAnswer).filter(
        UserQuestionAnswer.account_id == user.account_id,
        UserQuestionAnswer.date == today,
        UserQuestionAnswer.is_correct == True
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
        DailyQuestion.question_number == question_number,
        DailyQuestion.date == today
    ).first()

    if not daily_question:
        raise HTTPException(status_code=400, detail="Question not allocated for today")

    # Check if user has already answered this question
    existing_answer = db.query(UserQuestionAnswer).filter(
        UserQuestionAnswer.account_id == user.account_id,
        UserQuestionAnswer.question_number == question_number,
        UserQuestionAnswer.date == today
    ).first()
    
    if existing_answer:
        raise HTTPException(status_code=400, detail="Question already attempted")

    # Validate answer format
    if not answer or not isinstance(answer, str):
        raise HTTPException(status_code=400, detail="Invalid answer format")
    
    if len(answer.strip()) == 0:
        raise HTTPException(status_code=400, detail="Answer cannot be empty")

    # Check answer
    is_correct = answer.lower() == question.correct_answer.lower()
    
    # Mark question as used in daily_question table
    daily_question.is_used = True
    
    # Create record in UserQuestionAnswer table
    try:
        # Create new record
        user_answer = UserQuestionAnswer(
            account_id=user.account_id,
            question_number=question_number,
            date=today,
            answer=answer,
            is_correct=is_correct,
            answered_at=datetime.utcnow(),
            is_common=daily_question.is_common
        )
        db.add(user_answer)
        logger.info(f"Created new UserQuestionAnswer record for user {user.account_id} on {today}")
    except Exception as e:
        logger.error(f"Error creating UserQuestionAnswer: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to record your answer")
    
    # Update the Entry table for reward eligibility
    try:
        entry = db.query(Entry).filter(
            Entry.account_id == user.account_id,
            Entry.date == today
        ).first()
        
        if not entry:
            # Create new entry for the day
            entry = Entry(
                account_id=user.account_id,
                ques_attempted=1,
                correct_answers=1 if is_correct else 0,
                wrong_answers=0 if is_correct else 1,
                date=today
            )
            db.add(entry)
            logger.info(f"Created new entry for user {user.account_id} on {today}")
        else:
            # Update existing entry - increment the counts properly
            entry.ques_attempted += 1
            if is_correct:
                entry.correct_answers += 1
            else:
                entry.wrong_answers += 1
            logger.info(f"Updated entry for user {user.account_id} on {today}, ques_attempted={entry.ques_attempted}, correct={entry.correct_answers}, wrong={entry.wrong_answers}")
    except Exception as e:
        logger.error(f"Error updating Entry table: {str(e)}")
        # Continue even if there's an error here - this shouldn't block the main flow
    
    # --- ADD GEM AWARD LOGIC --- #
    gems_awarded_this_answer = 0
    try:
        if is_correct:
            gems_awarded_this_answer = 1 # Award 1 gem for correct answer
            user.gems += gems_awarded_this_answer
            logger.info(f"User {user.account_id} correct. Awarded {gems_awarded_this_answer} gem. Current gems: {user.gems}")
        else:
            logger.info(f"User {user.account_id} incorrect. No gems awarded.")
    except Exception as e:
        logger.error(f"Error processing gem award: {str(e)}")
        # Continue even if there's an error here

    # Commit all changes
    try:
        db.commit()
        logger.info(f"Committed updates for user {user.account_id} submission.")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to commit submission for user {user.account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save your answer.")

    # Return result
    return {
        "message": "Answer submitted successfully.",
        "is_correct": is_correct,
        "correct_answer": question.correct_answer,
        "explanation": question.explanation,
        "gems_awarded": gems_awarded_this_answer,
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

@router.get("/boost-status", response_model=BoostStatusResponse)
async def get_boost_status(
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get the current status and counts of the user's gameplay boosts."""
    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check for availability (must have count > 0 AND not used today)
    hint_available = user.hint_count > 0 and not user.hint_used_today
    fifty_fifty_available = user.fifty_fifty_count > 0 and not user.fifty_fifty_used_today
    auto_answer_available = user.auto_answer_count > 0 and not user.auto_answer_used_today
    
    return BoostStatusResponse(
        streak_saver_count=user.streak_saver_count,
        question_reroll_count=user.question_reroll_count,
        extra_chance_count=user.extra_chance_count,
        hint_count=user.hint_count,
        fifty_fifty_count=user.fifty_fifty_count,
        auto_answer_count=user.auto_answer_count,
        hint_available_today=hint_available,
        fifty_fifty_available_today=fifty_fifty_available,
        auto_answer_available_today=auto_answer_available
    )

@router.post("/reroll-question")
async def reroll_question(
    current_question_number: int,
    payment_type: str = "boost",  # Default to using boost count if available
    check_expiration: bool = None,  # Auth parameter (ignored)
    require_email: bool = None,  # Auth parameter (ignored)
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Reroll to get the next question in sequence.
    
    - Requires a question_reroll boost or payment with gems/USD
    - Marks current question as used and shows the next one
    - Default payment_type is 'boost' (uses available boost count)
    - USD payments deduct from wallet_balance, gems payments deduct from gems
    
    Parameters:
    - current_question_number: The question number to reroll from
    - payment_type: How to pay for the boost ('boost', 'gems', or 'usd')
    
    Returns the new question details and payment information.
    """
    logger = logging.getLogger(__name__)
    
    # Log received parameters for debugging
    logger.info(f"reroll_question called with: current_question_number={current_question_number}, payment_type={payment_type}")
    
    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Process the boost usage
    boost_result = process_boost_usage(
        user=user,
        boost_type="question_reroll",
        payment_type=payment_type,
        db=db,
        logger=logger
    )
    
    # Get today's date
    today = datetime.utcnow().date()
    
    # Get the current daily question
    current_daily_question = db.query(DailyQuestion).filter(
        DailyQuestion.question_number == current_question_number,
        DailyQuestion.date == today
    ).first()
    
    if not current_daily_question:
        raise HTTPException(status_code=404, detail="Current question not found")
    
    # Find the next question in order
    current_order = current_daily_question.question_order
    next_order = current_order + 1
    
    if next_order > 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No more questions available for reroll."
        )
    
    # Mark the current question as used (skipped)
    current_daily_question.is_used = True
    current_daily_question.was_changed = True
    
    # Get the next question
    next_question = db.query(DailyQuestion).filter(
        DailyQuestion.date == today,
        DailyQuestion.question_order == next_order
    ).first()
    
    if not next_question:
        raise HTTPException(status_code=404, detail="Next question not found")
    
    # Mark next question as used (it's being shown to the user)
    next_question.is_used = True
    
    # Get the trivia details for the next question
    question_details = db.query(Trivia).filter(
        Trivia.question_number == next_question.question_number
    ).first()
    
    # Commit the changes
    try:
        db.commit()
        logger.info(f"User {user.account_id} rerolled from question {current_question_number} to {next_question.question_number}")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to reroll question for user {user.account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reroll the question.")
    
    # Return the new question
    response = {
        "message": "Question rerolled successfully.",
        "gems_used": boost_result["used_gems"],
        "usd_used": boost_result["used_usd"],
        "remaining_rerolls": user.question_reroll_count,
        "current_gems": user.gems,
        "current_balance": user.wallet_balance,
        "question_number": question_details.question_number,
        "question": question_details.question,
        "options": {
            "a": question_details.option_a,
            "b": question_details.option_b,
            "c": question_details.option_c,
            "d": question_details.option_d
        },
        "category": question_details.category,
        "difficulty": question_details.difficulty_level,
        "picture_url": question_details.picture_url,
        "order": next_question.question_order,
        "is_common": next_question.is_common
    }
    
    return response

@router.post("/reset-unused-questions")
async def reset_unused_questions(
    db: Session = Depends(get_db)
):
    """
    Admin endpoint to reset unused questions from today's pool.
    
    - Marks unused questions (is_used=False) as available for future use
    - Sets question_done=False in the Trivia table for those questions
    - Deletes unused daily questions from the database
    - Typically used at the end of the day to recycle questions
    
    Returns summary statistics about the reset operation.
    """
    logger = logging.getLogger(__name__)
    logger.info(f"Starting reset of unused questions at {datetime.now()}")
    
    try:
        # Get today's date
        today = datetime.utcnow().date()
        
        # Get all daily questions from today
        today_questions = db.query(DailyQuestion).filter(
            DailyQuestion.date == today
        ).all()
        
        # Count total, used, and unused questions
        total_questions = len(today_questions)
        used_questions = sum(1 for q in today_questions if q.is_used)
        unused_questions = total_questions - used_questions
        
        logger.info(f"Found {total_questions} questions for today, {used_questions} used and {unused_questions} unused")
        
        # Collect unused questions for deletion
        unused_question_ids = []
        processed_count = 0
        
        for daily_q in today_questions:
            if not daily_q.is_used:
                # Get the Trivia question
                trivia_q = db.query(Trivia).filter(
                    Trivia.question_number == daily_q.question_number
                ).first()
                
                if trivia_q:
                    # Mark as undone so it can be used again in the future
                    trivia_q.question_done = False
                    processed_count += 1
                    logger.info(f"Marked question {trivia_q.question_number} as undone")
                    
                # Add to list for deletion
                unused_question_ids.append(daily_q.id)
        
        # Delete unused daily questions
        if unused_question_ids:
            deletion_count = db.query(DailyQuestion).filter(
                DailyQuestion.id.in_(unused_question_ids)
            ).delete(synchronize_session=False)
            logger.info(f"Deleted {deletion_count} unused daily questions")
        
        # Commit the changes
        db.commit()
        logger.info(f"Successfully reset and deleted {processed_count} unused questions")
        
        return {
            "message": f"Successfully reset and deleted {processed_count} unused questions",
            "total_questions": total_questions,
            "used_questions": used_questions,
            "unused_questions": unused_questions,
            "processed_count": processed_count,
            "deleted_questions": len(unused_question_ids)
        }
    
    except Exception as e:
        db.rollback()
        logger.error(f"Error resetting unused questions: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to reset unused questions: {str(e)}")

@router.post("/use-hint")
async def use_hint(
    question_number: int,
    payment_type: str = "boost",  # Default to using boost count if available
    check_expiration: bool = None,  # Auth parameter (ignored)
    require_email: bool = None,  # Auth parameter (ignored)
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Provides a hint for the current question.
    
    - Gives a clue about the correct answer
    - Costs 30 gems (from store_items.json) if paying with gems
    - Can also pay with USD (deducts from wallet_balance)
    - Limited to one use per day when using boost count
    
    Parameters:
    - question_number: The question to get a hint for
    - payment_type: How to pay for the hint ('boost', 'gems', or 'usd')
    
    Returns the hint and payment information.
    """
    logger = logging.getLogger(__name__)
    
    # Log received parameters for debugging
    logger.info(f"use_hint called with: question_number={question_number}, payment_type={payment_type}")
    
    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Process the boost usage
    boost_result = process_boost_usage(
        user=user,
        boost_type="hint",
        payment_type=payment_type,
        db=db,
        logger=logger
    )
    
    # Get the question
    question = db.query(Trivia).filter(Trivia.question_number == question_number).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Use the explanation from the question as the hint
    hint = question.explanation
    
    # Commit the changes
    try:
        db.commit()
        logger.info(f"User {user.account_id} used hint for question {question_number}")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to process hint for user {user.account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process hint.")
    
    return {
        "message": "Hint provided successfully.",
        "hint": hint,
        "gems_used": boost_result["used_gems"],
        "usd_used": boost_result["used_usd"],
        "remaining_hints": user.hint_count,
        "current_gems": user.gems,
        "current_balance": user.wallet_balance,
        "hint_used_today": user.hint_used_today
    }

@router.post("/use-fifty-fifty")
async def use_fifty_fifty(
    question_number: int,
    payment_type: str = "boost",  # Default to using boost count if available
    check_expiration: bool = None,  # Auth parameter (ignored)
    require_email: bool = None,  # Auth parameter (ignored)
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Eliminates two incorrect answers, leaving only the correct answer and one wrong option.
    
    - Reduces options from 4 to 2, significantly improving odds
    - Costs 50 gems (from store_items.json) if paying with gems
    - Can also pay with USD (deducts from wallet_balance)
    - Limited to one use per day when using boost count
    
    Parameters:
    - question_number: The question to use fifty-fifty on
    - payment_type: How to pay for the boost ('boost', 'gems', or 'usd')
    
    Returns the remaining options and payment information.
    """
    logger = logging.getLogger(__name__)
    
    # Log received parameters for debugging
    logger.info(f"use_fifty_fifty called with: question_number={question_number}, payment_type={payment_type}")
    
    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Process the boost usage
    boost_result = process_boost_usage(
        user=user,
        boost_type="fifty_fifty",
        payment_type=payment_type,
        db=db,
        logger=logger
    )
    
    # Get the question
    question = db.query(Trivia).filter(Trivia.question_number == question_number).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    # Determine which options to keep
    correct_answer = question.correct_answer.lower()
    all_options = {
        'a': question.option_a,
        'b': question.option_b,
        'c': question.option_c,
        'd': question.option_d
    }
    
    # Find the correct option label
    correct_option = None
    for opt, value in all_options.items():
        if value.lower() == correct_answer.lower():
            correct_option = opt
            break
    
    if not correct_option:
        logger.error(f"Could not find correct option for answer: {correct_answer}")
        correct_option = "a"  # Fallback
    
    # Get incorrect options (excluding the correct one)
    incorrect_options = [opt for opt in all_options.keys() if opt != correct_option]
    
    # Randomly select one incorrect option
    random_incorrect = random.choice(incorrect_options)
    
    # Our final options are the correct one and one random incorrect one
    keep_option_labels = [correct_option, random_incorrect]
    
    # Commit the changes
    try:
        db.commit()
        logger.info(f"User {user.account_id} used fifty-fifty for question {question_number}")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to process fifty-fifty for user {user.account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process fifty-fifty.")
    
    return {
        "message": "Fifty-fifty used successfully.",
        "keep_options": sorted(keep_option_labels),  # Sort to not give away the answer by order
        "option_values": {
            opt: all_options[opt] for opt in keep_option_labels
        },
        "gems_used": boost_result["used_gems"],
        "usd_used": boost_result["used_usd"],
        "remaining_fifty_fifty": user.fifty_fifty_count,
        "current_gems": user.gems,
        "current_balance": user.wallet_balance,
        "fifty_fifty_used_today": user.fifty_fifty_used_today
    }

@router.post("/use-auto-answer")
async def use_auto_answer(
    question_number: int,
    payment_type: str = "boost",  # Default to using boost count if available
    check_expiration: bool = None,  # Auth parameter (ignored)
    require_email: bool = None,  # Auth parameter (ignored)
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Automatically answers the question correctly without user input.
    
    - Guarantees a correct answer for the specified question
    - Costs 300 gems (from store_items.json) if paying with gems
    - Can also pay with USD (deducts from wallet_balance)
    - Limited to one use per day when using boost count
    - Gives 1 gem reward as normal for correct answers
    
    Parameters:
    - question_number: The question to auto-answer
    - payment_type: How to pay for the boost ('boost', 'gems', or 'usd')
    
    Returns the correct answer, explanation, and payment information.
    """
    logger = logging.getLogger(__name__)
    
    # Log received parameters for debugging
    logger.info(f"use_auto_answer called with: question_number={question_number}, payment_type={payment_type}")
    
    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Process the boost usage
    boost_result = process_boost_usage(
        user=user,
        boost_type="auto_answer",  # Use model's naming, will be mapped to store_config's auto_submit
        payment_type=payment_type,
        db=db,
        logger=logger
    )
    
    # Get today's date
    today = datetime.utcnow().date()
    
    # Get the question and daily question
    question = db.query(Trivia).filter(Trivia.question_number == question_number).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    
    daily_question = db.query(DailyQuestion).filter(
        DailyQuestion.question_number == question_number,
        DailyQuestion.date == today
    ).first()
    
    if not daily_question:
        raise HTTPException(status_code=400, detail="Question not allocated for today")
    
    # Check if user has already answered this question
    existing_answer = db.query(UserQuestionAnswer).filter(
        UserQuestionAnswer.account_id == user.account_id,
        UserQuestionAnswer.question_number == question_number,
        UserQuestionAnswer.date == today
    ).first()
    
    if existing_answer:
        raise HTTPException(status_code=400, detail="Question already attempted")
    
    # Mark question as used and answered correctly
    daily_question.is_used = True
    
    # Set the answer to be correct
    correct_answer = question.correct_answer
    
    # Create record in UserQuestionAnswer table
    try:
        # Create new record
        user_answer = UserQuestionAnswer(
            account_id=user.account_id,
            question_number=question_number,
            date=today,
            answer=correct_answer,
            is_correct=True,
            answered_at=datetime.utcnow(),
            is_common=daily_question.is_common
        )
        db.add(user_answer)
        logger.info(f"Created new UserQuestionAnswer record via auto-answer for user {user.account_id} on {today}")
    except Exception as e:
        logger.error(f"Error creating UserQuestionAnswer via auto-answer: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to record your answer")
    
    # Update the Entry table for reward eligibility
    try:
        entry = db.query(Entry).filter(
            Entry.account_id == user.account_id,
            Entry.date == today
        ).first()
        
        if not entry:
            # Create new entry for the day
            entry = Entry(
                account_id=user.account_id,
                ques_attempted=1,
                correct_answers=1,
                wrong_answers=0,
                date=today
            )
            db.add(entry)
            logger.info(f"Created new entry for user {user.account_id} on {today}")
        else:
            # Update existing entry - increment the counts properly
            entry.ques_attempted += 1
            entry.correct_answers += 1
            logger.info(f"Updated entry for user {user.account_id} on {today}, ques_attempted={entry.ques_attempted}, correct={entry.correct_answers}, wrong={entry.wrong_answers}")
    except Exception as e:
        logger.error(f"Error updating Entry table via auto-answer: {str(e)}")
    
    # Award gem for correct answer (same as normal correct answer)
    gems_awarded = 1
    user.gems += gems_awarded
    
    # Commit the changes
    try:
        db.commit()
        logger.info(f"User {user.account_id} used auto-answer for question {question_number}")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to process auto-answer for user {user.account_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to process auto-answer.")
    
    return {
        "message": "Auto-answer used successfully.",
        "question_number": question_number,
        "correct_answer": correct_answer,
        "explanation": question.explanation,
        "gems_used": boost_result["used_gems"],
        "usd_used": boost_result["used_usd"],
        "gems_awarded": gems_awarded,
        "net_gems": gems_awarded - boost_result["used_gems"],
        "remaining_auto_answers": user.auto_answer_count,
        "current_gems": user.gems,
        "current_balance": user.wallet_balance,
        "auto_answer_used_today": user.auto_answer_used_today
    }

