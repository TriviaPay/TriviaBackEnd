from fastapi import APIRouter, Depends, HTTPException, status, Path, Body
from sqlalchemy.orm import Session
from typing import Optional, Literal, Dict, Any
from pydantic import BaseModel, Field
import json
import random
from datetime import datetime
from pathlib import Path as FilePath
from sqlalchemy.sql import func

from db import get_db
from models import User, Trivia, DailyQuestion
from routers.dependencies import get_current_user

router = APIRouter(prefix="/store", tags=["Store"])

# Load store configuration
STORE_CONFIG_PATH = FilePath("config/store_items.json")
with open(STORE_CONFIG_PATH) as f:
    store_config = json.load(f)

class PurchaseRequest(BaseModel):
    """Model for purchase requests"""
    payment_type: str = Field(
        ...,
        description="Type of payment to use for the purchase",
        example="gems"
    )

    class Config:
        schema_extra = {
            "example": {
                "payment_type": "gems"
            }
        }

class PurchaseResponse(BaseModel):
    """Model for purchase responses"""
    success: bool
    remaining_gems: Optional[int] = None
    remaining_balance: Optional[float] = None
    message: str

class UseBoostRequest(BaseModel):
    """Model for using a gameplay boost"""
    boost_type: str = Field(
        ...,
        description="Type of boost to use",
        example="hint"
    )
    payment_type: str = Field(
        ...,
        description="Type of payment to use for the purchase",
        example="gems"
    )
    question_number: Optional[int] = Field(
        None,
        description="Question number to use the boost on",
        example=1
    )

    class Config:
        schema_extra = {
            "example": {
                "boost_type": "hint",
                "payment_type": "gems",
                "question_number": 1
            }
        }

def validate_and_process_cosmetic_purchase(
    item_id: str,
    payment_type: str,
    user: User,
    db: Session
) -> PurchaseResponse:
    """
    Validate and process a cosmetic purchase
    """
    # Check if item exists
    if item_id not in store_config["cosmetics"]:
        raise HTTPException(status_code=404, detail=f"Cosmetic {item_id} not found")
    
    item = store_config["cosmetics"][item_id]
    
    # Validate payment type
    if payment_type == 'gems':
        if 'gems' not in item:
            raise HTTPException(status_code=400, detail="Item cannot be purchased with gems")
        
        cost = item['gems']
        if user.gems < cost:
            raise HTTPException(status_code=400, detail="Insufficient gems")
        
        # Deduct gems
        user.gems -= cost
        
        # Update user's inventory
        owned_cosmetics = json.loads(user.owned_cosmetics or '{}')
        owned_cosmetics[item_id] = True
        user.owned_cosmetics = json.dumps(owned_cosmetics)
        
        db.commit()
        
        return PurchaseResponse(
            success=True,
            remaining_gems=user.gems,
            message=f"Successfully purchased {item_id} for {cost} gems"
        )
    
    elif payment_type == 'usd':
        if 'usd' not in item:
            raise HTTPException(status_code=400, detail="Item cannot be purchased with USD")
        
        cost = item['usd']
        if user.balance < cost:
            raise HTTPException(status_code=400, detail="Insufficient balance")
        
        # Deduct balance
        user.balance -= cost
        
        # Update user's inventory
        owned_cosmetics = json.loads(user.owned_cosmetics or '{}')
        owned_cosmetics[item_id] = True
        user.owned_cosmetics = json.dumps(owned_cosmetics)
        
        db.commit()
        
        return PurchaseResponse(
            success=True,
            remaining_balance=user.balance,
            message=f"Successfully purchased {item_id} for ${cost}"
        )
    
    else:
        raise HTTPException(status_code=400, detail="Invalid payment type")

@router.post("/cosmetics/{item_id}", response_model=PurchaseResponse)
async def purchase_cosmetic(
    item_id: str = Path(..., description="ID of the cosmetic item to purchase", example="avatar_pack"),
    purchase: PurchaseRequest = Body(..., description="Purchase details"),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Purchase a cosmetic item"""
    user = db.query(User).filter(User.sub == claims.get("sub")).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return validate_and_process_cosmetic_purchase(
        item_id=item_id,
        payment_type=purchase.payment_type,
        user=user,
        db=db
    )

@router.post("/gameplay-boosts")
async def use_gameplay_boost(
    request: UseBoostRequest = Body(..., description="Boost usage details"),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Purchase and use a gameplay boost immediately"""
    sub = claims.get("sub")
    user = db.query(User).filter(User.sub == sub).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Validate boost type exists
    if request.boost_type not in store_config["gameplay_boosts"]:
        raise HTTPException(status_code=404, detail=f"Boost {request.boost_type} not found")

    item = store_config["gameplay_boosts"][request.boost_type]

    # Validate and process payment
    if request.payment_type == 'gems':
        if 'gems' not in item:
            raise HTTPException(status_code=400, detail="Boost cannot be purchased with gems")
        
        cost = item['gems']
        if user.gems < cost:
            raise HTTPException(status_code=400, detail="Insufficient gems")
        
        # Deduct gems (will commit after boost is used)
        user.gems -= cost
        
    elif request.payment_type == 'usd':
        if 'usd' not in item:
            raise HTTPException(status_code=400, detail="Boost cannot be purchased with USD")
        
        cost = item['usd']
        if user.balance < cost:
            raise HTTPException(status_code=400, detail="Insufficient balance")
        
        # Deduct balance (will commit after boost is used)
        user.balance -= cost
    
    else:
        raise HTTPException(status_code=400, detail="Invalid payment type")

    # Handle streak saver separately as it doesn't need a question
    if request.boost_type == "streak_saver":
        if not user.last_streak_date:
            raise HTTPException(status_code=400, detail="No active streak to save")
        
        today = datetime.utcnow().date()
        if user.last_streak_date.date() == today:
            raise HTTPException(status_code=400, detail="Already logged in today")
        
        # Save the streak by updating last_streak_date to yesterday
        user.last_streak_date = datetime.utcnow().replace(day=today.day-1)
        db.commit()
        
        return {
            "message": "Streak saved successfully",
            "remaining_gems": user.gems if request.payment_type == 'gems' else None,
            "remaining_balance": user.balance if request.payment_type == 'usd' else None
        }

    # For all other boosts, we need a question number
    if not request.question_number:
        raise HTTPException(status_code=400, detail="Question number is required for this boost type")

    # Get the question
    question = db.query(Trivia).filter(Trivia.question_number == request.question_number).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    # Get daily question allocation
    today = datetime.utcnow().date()
    daily_question = db.query(DailyQuestion).filter(
        DailyQuestion.account_id == user.account_id,
        DailyQuestion.question_number == request.question_number,
        func.date(DailyQuestion.date) == today
    ).first()

    if not daily_question:
        raise HTTPException(status_code=400, detail="Question not allocated for today")

    if daily_question.is_used and request.boost_type not in ["extra_chance"]:
        raise HTTPException(status_code=400, detail="Question already attempted")

    # Process boost
    response = {}
    if request.boost_type == "fifty_fifty":
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

    elif request.boost_type in ["question_reroll", "change_question"]:
        # Get a new unused question
        new_question = db.query(Trivia).filter(
            Trivia.question_done == False
        ).order_by(func.random()).first()

        if not new_question:
            raise HTTPException(status_code=400, detail="No questions available")

        # Update daily question
        daily_question.question_number = new_question.question_number
        daily_question.was_changed = True
        
        # Mark new question as used
        new_question.question_done = True
        new_question.que_displayed_date = datetime.utcnow()
        
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
            "picture_url": new_question.picture_url
        }

    elif request.boost_type == "hint":
        response = {
            "hint": question.explanation
        }
    
    elif request.boost_type == "extra_chance":
        if not daily_question.is_used:
            raise HTTPException(status_code=400, detail="Question hasn't been attempted yet")
        
        # Reset the question state completely
        daily_question.is_used = False
        daily_question.was_changed = False  # Reset any previous changes
        
        response = {
            "message": "Question has been reset for a fresh attempt",
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
                "picture_url": question.picture_url
            }
        }
    
    elif request.boost_type == "auto_submit":
        if daily_question.is_used:
            raise HTTPException(status_code=400, detail="Question already attempted")
        
        # Mark question as used and record correct answer
        daily_question.is_used = True
        
        # Return the correct answer and mark it as auto-submitted
        response = {
            "correct_answer": question.correct_answer,
            "explanation": question.explanation,
            "auto_submit": True,
            "is_correct": True  # Auto-submit always counts as correct
        }

        # Update user's progress (similar to submit-answer endpoint)
        # Note: We're marking it as correct since they paid for auto-submit
        daily_question.is_used = True
        daily_question.answer = question.correct_answer
        daily_question.is_correct = True
        daily_question.answered_at = datetime.utcnow()

    # Commit the transaction after boost is used
    db.commit()

    # Add payment info to response
    response["remaining_gems"] = user.gems if request.payment_type == 'gems' else None
    response["remaining_balance"] = user.balance if request.payment_type == 'usd' else None

    return response

@router.get("/items")
async def get_store_items():
    """Get all available store items"""
    return store_config 