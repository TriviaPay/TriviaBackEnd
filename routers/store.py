from fastapi import APIRouter, Depends, HTTPException, status, Path, Body, Query
from sqlalchemy.orm import Session
from typing import Optional, Literal, Dict, Any, List
from pydantic import BaseModel, Field
import json
import random
from datetime import datetime, date
from pathlib import Path as FilePath
from sqlalchemy.sql import func
import pytz
import os

from db import get_db
from models import User, Trivia, TriviaQuestionsDaily, TriviaQuestionsEntries, GemPackageConfig, BoostConfig, UserGemPurchase, TriviaUserDaily
from routers.dependencies import get_current_user, get_admin_user

router = APIRouter(prefix="/store", tags=["Store"])

# Helper function to get today's date in the app timezone (EST/US Eastern)
def get_today_in_app_timezone() -> date:
    """Get today's date in the app's timezone (EST/US Eastern)."""
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    return now.date()

# Load store configuration
STORE_CONFIG_PATH = FilePath("config/store_items.json")
with open(STORE_CONFIG_PATH) as f:
    store_config = json.load(f)

class PurchaseRequest(BaseModel):
    """Model for purchase requests"""
    payment_type: str = Field(
        ...,
        description="Type of payment to use for the purchase"
    )

    class Config:
        json_schema_extra = {
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
        description="Type of boost to use. Options: fifty_fifty, hint, change_question, auto_submit, extra_chance, streak_saver"
    )
    question_number: Optional[int] = Field(
        None,
        description="Question number to use the boost on (required for all boosts except streak_saver)"
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "boost_type": "hint",
                    "question_number": 1
                },
                {
                    "boost_type": "fifty_fifty", 
                    "question_number": 1
                },
                {
                    "boost_type": "auto_submit",
                    "question_number": 1
                },
                {
                    "boost_type": "streak_saver"
                }
            ]
        }

class BuyGemsRequest(BaseModel):
    """Model for buying gems with wallet balance"""
    package_id: int = Field(
        ...,
        description="ID of the gem package to purchase"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "package_id": 1
            }
        }

class GemPackageRequest(BaseModel):
    """Model for creating/updating a gem package"""
    price_usd: float = Field(
        ...,
        description="Price in USD"
    )
    gems_amount: int = Field(
        ...,
        description="Number of gems in the package"
    )
    is_one_time: bool = Field(
        False,
        description="Whether this is a one-time offer"
    )
    description: Optional[str] = Field(
        None,
        description="Description of the package"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "price_usd": 0.99,
                "gems_amount": 150,
                "is_one_time": False,
                "description": "Great value!"
            }
        }

class BoostConfigRequest(BaseModel):
    """Model for setting boost configuration"""
    boost_type: str = Field(
        ...,
        description="Type of boost"
    )
    gems_cost: int = Field(
        ...,
        description="Cost in gems"
    )
    description: Optional[str] = Field(
        None,
        description="Description of the boost"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "boost_type": "fifty_fifty",
                "gems_cost": 50,
                "description": "Remove two wrong answers"
            }
        }

class GemPackageResponse(BaseModel):
    """Model for gem package response"""
    id: int
    price_usd: float
    gems_amount: int
    is_one_time: bool
    description: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True

class BoostConfigResponse(BaseModel):
    """Model for boost config response"""
    boost_type: str
    gems_cost: int
    description: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True

 

@router.post("/gameplay-boosts", response_model=Dict[str, Any])
async def use_gameplay_boost(
    request: UseBoostRequest = Body(..., description="Boost usage details"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Purchase and use a gameplay boost immediately using gems.
    
    Available boost types:
    - **fifty_fifty** (50 gems): Remove two incorrect answer options
    - **hint** (30 gems): Get a hint for the current question
    - **change_question** (10 gems): Change to a different question (max 3 per day)
    - **auto_submit** (300 gems): Automatically submit the correct answer
    - **extra_chance** (150 gems): Reset a question for a fresh attempt after wrong answer
    - **streak_saver** (100 gems): Save your daily streak if you missed a day
    
    **Requirements:**
    - User must have sufficient gems
    - For question-based boosts: question_number must be provided
    - For change_question: User can only change 3 questions per day
    - For extra_chance: Question must have been attempted first
    
    **Response includes:**
    - Boost-specific data (hint, new question, etc.)
    - Remaining gems after purchase
    - Success confirmation
    """
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get boost config from database
    boost_config = db.query(BoostConfig).filter(BoostConfig.boost_type == request.boost_type).first()
    
    # If boost config is not in database, use default from config file as fallback
    if not boost_config:
        if request.boost_type not in store_config["gameplay_boosts"]:
            raise HTTPException(status_code=404, detail=f"Boost {request.boost_type} not found")
        
        item = store_config["gameplay_boosts"][request.boost_type]
        if 'gems' not in item:
            raise HTTPException(status_code=400, detail="Boost cannot be purchased with gems")
        
        cost = item['gems']
    else:
        cost = boost_config.gems_cost
    
    # Check if user has enough gems
    if user.gems < cost:
        raise HTTPException(status_code=400, detail=f"Insufficient gems. You have {user.gems} gems, but this boost costs {cost} gems")
    
    # Deduct gems (will commit after boost is used)
    user.gems -= cost

    # Handle streak saver separately as it doesn't need a question
    if request.boost_type == "streak_saver":
        if not user.last_streak_date:
            raise HTTPException(status_code=400, detail="No active streak to save")
        
        today = get_today_in_app_timezone()
        if user.last_streak_date.date() == today:
            raise HTTPException(status_code=400, detail="Already logged in today")
        
        # Save the streak by updating last_streak_date to yesterday
        user.last_streak_date = datetime.utcnow().replace(day=today.day-1)
        db.commit()
        
        return {
            "message": "Streak saved successfully",
            "remaining_gems": user.gems
        }

    # For all other boosts, we need a question number
    if not request.question_number:
        raise HTTPException(status_code=400, detail="Question number is required for this boost type")

    # Get the question
    question = db.query(Trivia).filter(Trivia.question_number == request.question_number).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    # Get user's daily question unlock/attempt
    today = get_today_in_app_timezone()
    user_daily = db.query(TriviaUserDaily).filter(
        TriviaUserDaily.account_id == user.account_id,
        TriviaUserDaily.question_number == request.question_number,
        TriviaUserDaily.date == today
    ).first()

    if not user_daily or user_daily.unlock_method is None:
        raise HTTPException(status_code=400, detail="Question not unlocked for today")

    if user_daily.status in ['answered_correct', 'answered_wrong'] and request.boost_type not in ["extra_chance"]:
        raise HTTPException(status_code=400, detail="Question already attempted")

    # Process boost
    response = {}
    
    # Get daily pool question for reference
    daily_pool_q = db.query(TriviaQuestionsDaily).filter(
        func.date(TriviaQuestionsDaily.date) == today,
        TriviaQuestionsDaily.question_number == request.question_number
    ).first()
    
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
        # Change question boost is deprecated - use /trivia/unlock-next instead
        raise HTTPException(
            status_code=400, 
            detail="Change question boost is deprecated. Use /trivia/unlock-next to unlock the next question in sequence."
        )

    elif request.boost_type == "hint":
        response = {
            "hint": question.explanation
        }
    
    elif request.boost_type == "extra_chance":
        # Check if question was answered wrong (can retry)
        if user_daily.status != 'answered_wrong':
            raise HTTPException(
                status_code=400, 
                detail=f"Question cannot be retried. Current status: {user_daily.status}. Only wrong answers can be retried."
            )
        
        # Reset the question state for retry
        user_daily.status = 'viewed'
        user_daily.user_answer = None
        user_daily.is_correct = None
        user_daily.answered_at = None
        user_daily.retry_count += 1
        
        response = {
            "message": "Question has been reset for a fresh attempt",
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
                "picture_url": question.picture_url
            }
        }
    
    elif request.boost_type == "auto_submit":
        if user_daily.status in ['answered_correct', 'answered_wrong']:
            raise HTTPException(status_code=400, detail="Question already attempted")
        
        # Mark question as answered with correct answer
        user_daily.user_answer = question.correct_answer
        user_daily.is_correct = True
        user_daily.answered_at = datetime.utcnow()
        user_daily.status = 'answered_correct'
        
        # Update entries
        entry = db.query(TriviaQuestionsEntries).filter(
            TriviaQuestionsEntries.account_id == user.account_id,
            TriviaQuestionsEntries.date == today
        ).first()
        
        if not entry:
            entry = TriviaQuestionsEntries(
                account_id=user.account_id,
                ques_attempted=1,
                correct_answers=1,
                wrong_answers=0,
                date=today
            )
            db.add(entry)
        else:
            entry.ques_attempted += 1
            entry.correct_answers += 1
        
        # Update eligibility
        from rewards_logic import update_user_eligibility
        update_user_eligibility(db, user.account_id, today)
        
        # Mark remaining questions as skipped
        remaining = db.query(TriviaUserDaily).filter(
            TriviaUserDaily.account_id == user.account_id,
            TriviaUserDaily.date == today,
            TriviaUserDaily.question_order > user_daily.question_order,
            TriviaUserDaily.status.notin_(['answered_correct', 'answered_wrong'])
        ).all()
        
        for rem in remaining:
            rem.status = 'skipped'
        
        # Return the correct answer and mark it as auto-submitted
        response = {
            "correct_answer": question.correct_answer,
            "explanation": question.explanation,
            "auto_submit": True,
            "is_correct": True  # Auto-submit always counts as correct
        }

    # Commit the transaction after boost is used
    db.commit()

    # Add payment info to response
    response["remaining_gems"] = user.gems

    return response

@router.post("/buy-gems", response_model=PurchaseResponse)
async def buy_gems_with_wallet(
    request: BuyGemsRequest = Body(..., description="Gem purchase details"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Buy gems using wallet balance"""
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get gem package from database
    gem_package = db.query(GemPackageConfig).filter(GemPackageConfig.id == request.package_id).first()
    if not gem_package:
        raise HTTPException(status_code=404, detail=f"Gem package with ID {request.package_id} not found")
    
    # Check if user has enough wallet balance
    if user.wallet_balance < gem_package.price_usd:
        raise HTTPException(
            status_code=400, 
            detail=f"Insufficient wallet balance. You have ${user.wallet_balance}, but this package costs ${gem_package.price_usd}"
        )
    
    # Check if this is a one-time offer that the user has already purchased
    if gem_package.is_one_time:
        # Check if THIS user has already purchased this one-time package
        existing_purchase = db.query(UserGemPurchase).filter(
            UserGemPurchase.user_id == user.account_id,
            UserGemPurchase.package_id == gem_package.id
        ).first()
        
        if existing_purchase:
            raise HTTPException(
                status_code=400,
                detail=f"You have already purchased this one-time offer on {existing_purchase.purchase_date}"
            )
    
    # Deduct from wallet and add gems
    user.wallet_balance -= gem_package.price_usd
    user.gems += gem_package.gems_amount
    user.last_wallet_update = datetime.utcnow()
    
    # Record the purchase in the user_gem_purchases table
    purchase_record = UserGemPurchase(
        user_id=user.account_id,
        package_id=gem_package.id,
        price_paid=gem_package.price_usd,
        gems_received=gem_package.gems_amount
    )
    db.add(purchase_record)
    
    db.commit()
    
    return PurchaseResponse(
        success=True,
        remaining_gems=user.gems,
        remaining_balance=user.wallet_balance,
        message=f"Successfully purchased {gem_package.gems_amount} gems for ${gem_package.price_usd}"
    )

@router.get("/gem-packages", response_model=List[GemPackageResponse])
async def get_gem_packages(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all available gem packages"""
    packages = db.query(GemPackageConfig).all()
    return packages

@router.get("/boost-configs", response_model=List[BoostConfigResponse])
async def get_boost_configs(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all boost configurations"""
    boosts = db.query(BoostConfig).all()
    return boosts

@router.post("/admin/gem-packages", response_model=GemPackageResponse)
async def create_gem_package(
    package: GemPackageRequest = Body(..., description="Gem package details"),
    claims: dict = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Admin endpoint to create a new gem package"""
    new_package = GemPackageConfig(
        price_usd=package.price_usd,
        gems_amount=package.gems_amount,
        is_one_time=package.is_one_time,
        description=package.description
    )
    
    db.add(new_package)
    db.commit()
    db.refresh(new_package)
    
    return new_package

@router.put("/admin/gem-packages/{package_id}", response_model=GemPackageResponse)
async def update_gem_package(
    package_id: int = Path(..., description="ID of the gem package to update"),
    package: GemPackageRequest = Body(..., description="Updated gem package details"),
    claims: dict = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Admin endpoint to update an existing gem package"""
    db_package = db.query(GemPackageConfig).filter(GemPackageConfig.id == package_id).first()
    if not db_package:
        raise HTTPException(status_code=404, detail=f"Gem package with ID {package_id} not found")
    
    # Update fields
    db_package.price_usd = package.price_usd
    db_package.gems_amount = package.gems_amount
    db_package.is_one_time = package.is_one_time
    db_package.description = package.description
    db_package.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(db_package)
    
    return db_package

@router.delete("/admin/gem-packages/{package_id}", response_model=Dict[str, Any])
async def delete_gem_package(
    package_id: int = Path(..., description="ID of the gem package to delete"),
    claims: dict = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Admin endpoint to delete a gem package"""
    db_package = db.query(GemPackageConfig).filter(GemPackageConfig.id == package_id).first()
    if not db_package:
        raise HTTPException(status_code=404, detail=f"Gem package with ID {package_id} not found")
    
    db.delete(db_package)
    db.commit()
    
    return {"message": f"Gem package with ID {package_id} deleted successfully"}

@router.post("/admin/boost-configs", response_model=BoostConfigResponse)
async def create_boost_config(
    boost: BoostConfigRequest = Body(..., description="Boost configuration details"),
    claims: dict = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Admin endpoint to create a new boost configuration"""
    # Check if boost config already exists
    existing = db.query(BoostConfig).filter(BoostConfig.boost_type == boost.boost_type).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Boost configuration for {boost.boost_type} already exists. Use PUT to update."
        )
    
    new_boost = BoostConfig(
        boost_type=boost.boost_type,
        gems_cost=boost.gems_cost,
        description=boost.description
    )
    
    db.add(new_boost)
    db.commit()
    db.refresh(new_boost)
    
    return new_boost

@router.put("/admin/boost-configs/{boost_type}", response_model=BoostConfigResponse)
async def update_boost_config(
    boost_type: str = Path(..., description="Type of boost to update"),
    boost: BoostConfigRequest = Body(..., description="Updated boost configuration details"),
    claims: dict = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Admin endpoint to update an existing boost configuration"""
    if boost_type != boost.boost_type:
        raise HTTPException(status_code=400, detail="Path boost_type does not match request body boost_type")
    
    db_boost = db.query(BoostConfig).filter(BoostConfig.boost_type == boost_type).first()
    if not db_boost:
        raise HTTPException(status_code=404, detail=f"Boost configuration for {boost_type} not found")
    
    # Update fields
    db_boost.gems_cost = boost.gems_cost
    db_boost.description = boost.description
    db_boost.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(db_boost)
    
    return db_boost

@router.delete("/admin/boost-configs/{boost_type}", response_model=Dict[str, Any])
async def delete_boost_config(
    boost_type: str = Path(..., description="Type of boost to delete"),
    claims: dict = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Admin endpoint to delete a boost configuration"""
    db_boost = db.query(BoostConfig).filter(BoostConfig.boost_type == boost_type).first()
    if not db_boost:
        raise HTTPException(status_code=404, detail=f"Boost configuration for {boost_type} not found")
    
    db.delete(db_boost)
    db.commit()
    
    return {"message": f"Boost configuration for {boost_type} deleted successfully"}

@router.get("/items")
async def get_store_items(user: User = Depends(get_current_user)):
    """Get all available store items"""
    return store_config 