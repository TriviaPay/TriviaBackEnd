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
from utils.storage import presign_get
import logging

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
    price_minor: int = Field(
        ...,
        description="Price in minor units (cents)"
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
    bucket: Optional[str] = Field(
        None,
        description="S3 bucket name for the package image"
    )
    object_key: Optional[str] = Field(
        None,
        description="S3 object key for the package image"
    )
    mime_type: Optional[str] = Field(
        None,
        description="MIME type of the image (e.g., image/png, image/jpeg)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "price_minor": 99,
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
    url: Optional[str] = None  # Presigned S3 URL
    mime_type: Optional[str] = None  # MIME type of the image
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class BoostConfigResponse(BaseModel):
    """Model for boost config response"""
    boost_type: str
    gems_cost: int
    description: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

 

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
    - **extra_chance** (150 gems): Reset a question for a fresh attempt after wrong answer. Includes hint and correct answer.
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
    # Note: Streak system has been replaced with weekly daily login system
    # This boost is deprecated but kept for backward compatibility
    if request.boost_type == "streak_saver":
        raise HTTPException(
            status_code=400, 
            detail="Streak saver is no longer available. The streak system has been replaced with a weekly daily login reward system."
        )

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
        if user_daily.status == 'answered_correct':
            raise HTTPException(status_code=400, detail="Question already answered correctly")
        else:
            raise HTTPException(status_code=400, detail="Question already answered incorrectly")

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
        # correct_answer could be either the option letter (a/b/c/d) or the full text
        correct_option = None
        correct_answer_lower = question.correct_answer.lower().strip()
        
        # First check if correct_answer is just an option letter
        if correct_answer_lower in options:
            correct_option = correct_answer_lower
        else:
            # Otherwise, match against option text
            for opt in options:
                option_text = getattr(question, f"option_{opt}")
                if option_text and option_text.lower().strip() == correct_answer_lower:
                    correct_option = opt
                    break
        
        if not correct_option:
            raise HTTPException(
                status_code=500, 
                detail=f"Could not find correct option. Correct answer: {question.correct_answer}"
            )
            
        wrong_options = [opt for opt in options if opt != correct_option]
        random_wrong = random.choice(wrong_options)
        
        response = {
            "options": {
                correct_option: getattr(question, f"option_{correct_option}"),
                random_wrong: getattr(question, f"option_{random_wrong}")
            },
            "removed_options": [opt for opt in options if opt not in [correct_option, random_wrong]]
        }

    elif request.boost_type in ["question_reroll", "change_question"]:
        # Change question boost is deprecated - use /trivia/unlock-next instead
        raise HTTPException(
            status_code=400, 
            detail="Change question boost is deprecated. Use /trivia/unlock-next to unlock the next question in sequence."
        )

    elif request.boost_type == "hint":
        # Use hint field if available, otherwise fall back to explanation
        hint_text = question.hint if question.hint else question.explanation
        response = {
            "hint": hint_text
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
        
        # Get hint (use hint field if available, otherwise fall back to explanation)
        hint_text = question.hint if question.hint else question.explanation
        
        # Get correct answer (handle both option letter and full text)
        correct_answer_lower = question.correct_answer.lower().strip()
        options = ["a", "b", "c", "d"]
        
        # If correct_answer is an option letter, get the full option text
        if correct_answer_lower in options:
            correct_answer_text = getattr(question, f"option_{correct_answer_lower}")
            correct_answer_letter = correct_answer_lower
        else:
            # Otherwise use the correct_answer as-is and find matching option letter
            correct_answer_text = question.correct_answer
            correct_answer_letter = None
            for opt in options:
                option_text = getattr(question, f"option_{opt}")
                if option_text and option_text.lower().strip() == correct_answer_lower:
                    correct_answer_letter = opt
                    break
        
        response = {
            "message": "Question has been reset for a fresh attempt",
            "retry_count": user_daily.retry_count,
            "hint": hint_text,
            "correct_answer": {
                "letter": correct_answer_letter,
                "text": correct_answer_text
            },
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
            if user_daily.status == 'answered_correct':
                raise HTTPException(status_code=400, detail="Question already answered correctly")
            else:
                raise HTTPException(status_code=400, detail="Question already answered incorrectly")
        
        # Determine the actual answer text to store
        # correct_answer could be either the option letter (a/b/c/d) or the full text
        correct_answer_lower = question.correct_answer.lower().strip()
        options = ["a", "b", "c", "d"]
        
        # If correct_answer is an option letter, get the full option text
        if correct_answer_lower in options:
            answer_to_store = getattr(question, f"option_{correct_answer_lower}")
        else:
            # Otherwise use the correct_answer as-is
            answer_to_store = question.correct_answer
        
        # Mark question as answered with correct answer
        user_daily.user_answer = answer_to_store
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
            "correct_answer": answer_to_store,  # Return the actual answer text that was stored
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
    
    # Get price in minor units
    price_minor = gem_package.price_minor if gem_package.price_minor is not None else 0
    price_usd_display = price_minor / 100.0
    
    # Check wallet balance (use wallet_balance_minor if available)
    wallet_balance_minor = user.wallet_balance_minor if user.wallet_balance_minor is not None else int((user.wallet_balance or 0) * 100)
    
    if wallet_balance_minor < price_minor:
        raise HTTPException(
            status_code=400, 
            detail=f"Insufficient wallet balance. You have ${wallet_balance_minor / 100.0:.2f}, but this package costs ${price_usd_display:.2f}"
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
    # Update wallet_balance_minor if available, otherwise use wallet_balance
    if user.wallet_balance_minor is not None:
        user.wallet_balance_minor -= price_minor
        user.wallet_balance = user.wallet_balance_minor / 100.0  # Keep in sync
    else:
        user.wallet_balance = (wallet_balance_minor - price_minor) / 100.0
    
    user.gems += gem_package.gems_amount
    user.last_wallet_update = datetime.utcnow()
    
    # Record the purchase in the user_gem_purchases table
    purchase_record = UserGemPurchase(
        user_id=user.account_id,
        package_id=gem_package.id,
        price_paid=price_usd_display,
        gems_received=gem_package.gems_amount
    )
    db.add(purchase_record)
    
    db.commit()
    
    remaining_balance = user.wallet_balance_minor / 100.0 if user.wallet_balance_minor is not None else user.wallet_balance
    
    return PurchaseResponse(
        success=True,
        remaining_gems=user.gems,
        remaining_balance=remaining_balance,
        message=f"Successfully purchased {gem_package.gems_amount} gems for ${price_usd_display:.2f}"
    )

@router.get("/gem-packages", response_model=List[GemPackageResponse])
async def get_gem_packages(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all available gem packages with presigned URLs for images"""
    packages = db.query(GemPackageConfig).all()
    result = []
    for pkg in packages:
        # Generate presigned URL if bucket and object_key are present
        signed_url = None
        if pkg.bucket and pkg.object_key:
            try:
                signed_url = presign_get(pkg.bucket, pkg.object_key, expires=900)  # 15 minutes
                if not signed_url:
                    logging.warning(f"presign_get returned None for gem package {pkg.id} with bucket={pkg.bucket}, key={pkg.object_key}")
            except Exception as e:
                logging.error(f"Failed to presign gem package {pkg.id}: {e}", exc_info=True)
        else:
            logging.debug(f"Gem package {pkg.id} missing bucket/object_key: bucket={pkg.bucket}, object_key={pkg.object_key}")
        
        # Use price_minor if available, otherwise fall back to price_usd
        price_minor = pkg.price_minor if pkg.price_minor is not None else 0
        price_usd_display = price_minor / 100.0 if price_minor else 0.0
        
        result.append(GemPackageResponse(
            id=pkg.id,
            product_id=getattr(pkg, 'product_id', None),
            price_usd=price_usd_display,
            price_minor=price_minor,
            gems_amount=pkg.gems_amount,
            is_one_time=pkg.is_one_time,
            description=pkg.description,
            url=signed_url,
            mime_type=pkg.mime_type,
            created_at=pkg.created_at,
            updated_at=pkg.updated_at
        ))
    return result

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
        description=package.description,
        bucket=package.bucket,
        object_key=package.object_key,
        mime_type=package.mime_type
    )
    
    db.add(new_package)
    db.commit()
    db.refresh(new_package)
    
    # Generate presigned URL if bucket and object_key are present
    signed_url = None
    if new_package.bucket and new_package.object_key:
        try:
            signed_url = presign_get(new_package.bucket, new_package.object_key, expires=900)
        except Exception as e:
            logging.error(f"Failed to presign gem package {new_package.id}: {e}", exc_info=True)
    
    return GemPackageResponse(
        id=new_package.id,
        price_usd=new_package.price_usd,
        gems_amount=new_package.gems_amount,
        is_one_time=new_package.is_one_time,
        description=new_package.description,
        url=signed_url,
        mime_type=new_package.mime_type,
        created_at=new_package.created_at,
        updated_at=new_package.updated_at
    )

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
    db_package.price_minor = package.price_minor
    db_package.gems_amount = package.gems_amount
    db_package.is_one_time = package.is_one_time
    db_package.description = package.description
    db_package.bucket = package.bucket
    db_package.object_key = package.object_key
    db_package.mime_type = package.mime_type
    db_package.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(db_package)
    
    # Generate presigned URL if bucket and object_key are present
    signed_url = None
    if db_package.bucket and db_package.object_key:
        try:
            signed_url = presign_get(db_package.bucket, db_package.object_key, expires=900)
        except Exception as e:
            logging.error(f"Failed to presign gem package {db_package.id}: {e}", exc_info=True)
    
    return GemPackageResponse(
        id=db_package.id,
        price_usd=db_package.price_usd,
        gems_amount=db_package.gems_amount,
        is_one_time=db_package.is_one_time,
        description=db_package.description,
        url=signed_url,
        mime_type=db_package.mime_type,
        created_at=db_package.created_at,
        updated_at=db_package.updated_at
    )

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