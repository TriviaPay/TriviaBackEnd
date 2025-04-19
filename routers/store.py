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

# Load store configuration
STORE_CONFIG_PATH = FilePath("config/store_items.json")
with open(STORE_CONFIG_PATH) as f:
    store_config = json.load(f)

# Create a router with enhanced documentation
router = APIRouter(
    prefix="/store", 
    tags=["Store"],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "User not authenticated"},
        status.HTTP_403_FORBIDDEN: {"description": "User not authorized"},
        status.HTTP_404_NOT_FOUND: {"description": "Item not found"},
        status.HTTP_400_BAD_REQUEST: {"description": "Invalid request parameters or insufficient funds"}
    }
)

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

class UseBoostRequest(BaseModel):
    """Model for using a gameplay boost"""
    boost_type: str = Field(
        ..., 
        description="Type of boost to use. Available options: 'streak_saver', 'question_reroll', 'extra_chance', 'hint', 'fifty_fifty', 'auto_answer'",
        example="streak_saver"
    )
    payment_type: str = Field(
        ..., 
        description="Type of payment (gems or usd)",
        example="gems"
    )
    question_number: Optional[int] = Field(
        None, 
        description="Question number (required for question-related boosts)",
        example=123
    )
    use_immediately: bool = Field(
        True, 
        description="Whether to use the boost immediately or just purchase it",
        example=False
    )

    class Config:
        schema_extra = {
            "example": {
                "boost_type": "streak_saver",
                "payment_type": "gems",
                "use_immediately": False
            }
        }

class PurchaseResponse(BaseModel):
    """Model for purchase responses"""
    success: bool = Field(..., description="Whether the purchase was successful")
    remaining_gems: Optional[int] = Field(None, description="Remaining gems after purchase if paid with gems")
    remaining_balance: Optional[float] = Field(None, description="Remaining wallet balance after purchase if paid with USD")
    message: str = Field(..., description="A descriptive message about the purchase")

    class Config:
        schema_extra = {
            "example": {
                "success": True,
                "remaining_gems": 250,
                "message": "Successfully purchased streak_saver"
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
        if user.wallet_balance < cost:
            raise HTTPException(status_code=400, detail="Insufficient wallet balance")
        
        # Deduct balance
        user.wallet_balance -= cost
        
        # Update user's inventory
        owned_cosmetics = json.loads(user.owned_cosmetics or '{}')
        owned_cosmetics[item_id] = True
        user.owned_cosmetics = json.dumps(owned_cosmetics)
        
        db.commit()
        
        return PurchaseResponse(
            success=True,
            remaining_balance=user.wallet_balance,
            message=f"Successfully purchased {item_id} for ${cost}"
        )
    
    else:
        raise HTTPException(status_code=400, detail="Invalid payment type")

@router.post(
    "/cosmetics/{item_id}", 
    response_model=PurchaseResponse,
    summary="Purchase a cosmetic item",
    description="""
    Purchase a cosmetic item using either gems or USD.
    Available cosmetics: 'avatar_pack', 'answer_button_skins', 'confetti_win_fx', 'profile_borders', 'chat_bubble_skins', 'reaction_emojis'.
    Each item has a different cost in gems or USD and will be added to the user's inventory once purchased.
    """
)
async def purchase_cosmetic(
    item_id: str = Path(..., description="ID of the cosmetic item to purchase", example="profile_borders"),
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

@router.post(
    "/gameplay-boosts", 
    response_model=PurchaseResponse,
    summary="Purchase and optionally use a gameplay boost",
    description="""
    Purchase and optionally use a gameplay boost. Available boost types:
    
    - **streak_saver**: Preserves streak after missing a day. Costs 100 gems or $0.49.
    - **question_reroll**: Changes to a different question. Costs 80 gems.
    - **extra_chance**: Extra attempt if you answer wrong. Costs 150 gems or $0.99.
    - **hint**: Get a hint for the current question. Costs 30 gems.
    - **fifty_fifty**: Removes two wrong answers. Costs 50 gems.
    - **auto_answer**: Automatically answers correctly. Costs 300 gems.
    
    Set `use_immediately=false` to add the boost to your inventory without using it immediately.
    When `use_immediately=true`, question_number is required for question-related boosts.
    """
)
async def purchase_gameplay_boost(
    request: UseBoostRequest = Body(..., description="Boost purchase details"),
    claims: dict = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Purchase a gameplay boost. 
    Set use_immediately=false to only add it to inventory without using it.
    """
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
        
        # Deduct gems
        user.gems -= cost
        
    elif request.payment_type == 'usd':
        if 'usd' not in item:
            raise HTTPException(status_code=400, detail="Boost cannot be purchased with USD")
        
        cost = item['usd']
        if user.wallet_balance < cost:
            raise HTTPException(status_code=400, detail="Insufficient wallet balance")
        
        # Deduct balance
        user.wallet_balance -= cost
    
    else:
        raise HTTPException(status_code=400, detail="Invalid payment type")

    # Add the boost to the user's inventory
    if request.boost_type == "streak_saver":
        user.streak_saver_count += 1
        boost_count = user.streak_saver_count
    elif request.boost_type == "question_reroll":
        user.question_reroll_count += 1
        boost_count = user.question_reroll_count
    elif request.boost_type == "extra_chance":
        user.extra_chance_count += 1
        boost_count = user.extra_chance_count
    elif request.boost_type == "hint":
        user.hint_count += 1
        boost_count = user.hint_count
    elif request.boost_type == "fifty_fifty":
        user.fifty_fifty_count += 1
        boost_count = user.fifty_fifty_count
    elif request.boost_type == "auto_answer":
        user.auto_answer_count += 1
        boost_count = user.auto_answer_count
    else:
        raise HTTPException(status_code=400, detail=f"Unknown boost type: {request.boost_type}")

    # Commit the purchase
    db.commit()
    
    # If not using immediately, return the purchase response
    if not request.use_immediately:
        return PurchaseResponse(
            success=True,
            remaining_gems=user.gems if request.payment_type == 'gems' else None,
            remaining_balance=user.wallet_balance if request.payment_type == 'usd' else None,
            message=f"Successfully purchased {request.boost_type}. You now have {boost_count} available."
        )

    # Handle immediate usage of boost - this part only executes if use_immediately=true
    # For streak saver, check requirements for usage
    if request.boost_type == "streak_saver":
        if not user.last_streak_date:
            raise HTTPException(status_code=400, detail="No active streak to save")
        
        today = datetime.utcnow().date()
        if user.last_streak_date.date() == today:
            raise HTTPException(status_code=400, detail="Already logged in today")
        
        # Save the streak by updating last_streak_date to yesterday
        user.last_streak_date = datetime.utcnow().replace(day=today.day-1)
        
        # Decrement the counter since we're using one
        user.streak_saver_count -= 1
        
        db.commit()
        
        return PurchaseResponse(
            success=True,
            remaining_gems=user.gems if request.payment_type == 'gems' else None,
            remaining_balance=user.wallet_balance if request.payment_type == 'usd' else None,
            message="Streak saver purchased and used successfully"
        )

    # For all other boosts that require immediate usage, we need a question number
    if not request.question_number:
        raise HTTPException(status_code=400, detail="Question number is required for this boost type when use_immediately is true")

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

    # Process boost (rest of the implementation)
    # This part would handle the usage of other boosts for specific questions
    
    return PurchaseResponse(
        success=True,
        remaining_gems=user.gems if request.payment_type == 'gems' else None,
        remaining_balance=user.wallet_balance if request.payment_type == 'usd' else None,
        message=f"{request.boost_type} purchased and used successfully"
    )

@router.get(
    "/items",
    summary="Get all available store items",
    description="""
    Returns a complete catalog of all items available in the store, organized by category.
    The response includes:
    
    - **daily_rewards**: Information about daily login rewards and streak bonuses
    - **gameplay_boosts**: All available gameplay boosts and their costs in gems/USD
    - **cosmetics**: All available cosmetic items and their costs in gems/USD
    
    Each item includes a description and price information (gems and/or USD).
    """
)
async def get_store_items():
    """Get all available store items"""
    return store_config 