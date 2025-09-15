from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from db import get_db
from models import User
from routers.dependencies import get_current_user

router = APIRouter(prefix="/wallet", tags=["Wallet"])

class StripePaymentResponse(BaseModel):
    payment_intent_id: str
    amount: float
    status: str

class PurchaseRequest(BaseModel):
    item_id: str
    item_type: str  # e.g., "lifeline", "gems", "subscription"
    quantity: int = 1

@router.post("/add-funds")
async def add_funds_to_wallet(
    stripe_response: StripePaymentResponse,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Add funds to user's wallet after successful Stripe payment"""
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Verify payment status (you would typically verify with Stripe here)
    if stripe_response.status != "succeeded":
        raise HTTPException(status_code=400, detail="Payment not successful")

    # Add funds to wallet
    user.wallet_balance += stripe_response.amount
    user.last_wallet_update = datetime.utcnow()
    db.commit()

    return {
        "wallet_balance": user.wallet_balance,
        "amount_added": stripe_response.amount,
        "transaction_id": stripe_response.payment_intent_id
    }

@router.get("/balance")
async def get_wallet_balance(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user's current wallet balance"""
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "wallet_balance": user.wallet_balance,
        "total_spent": user.total_spent,
        "last_update": user.last_wallet_update
    }

@router.post("/purchase")
async def make_purchase(
    purchase: PurchaseRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Make a purchase using wallet balance"""
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Calculate cost based on item type and quantity
    cost = calculate_cost(purchase.item_type, purchase.item_id, purchase.quantity)
    
    # Check if user has enough balance
    if user.wallet_balance < cost:
        raise HTTPException(status_code=400, detail="Insufficient wallet balance")

    # Process the purchase
    try:
        # Deduct from wallet
        user.wallet_balance -= cost
        user.total_spent += cost
        user.last_wallet_update = datetime.utcnow()

        # Apply the purchase effects (e.g., add gems, lifelines, etc.)
        apply_purchase_effects(user, purchase)
        
        db.commit()
        
        return {
            "success": True,
            "remaining_balance": user.wallet_balance,
            "cost": cost,
            "item_type": purchase.item_type,
            "quantity": purchase.quantity
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

def calculate_cost(item_type: str, item_id: str, quantity: int) -> float:
    """Calculate the cost of a purchase based on item type and quantity"""
    # Define pricing for different items
    prices = {
        "lifeline": {
            "fifty-fifty": 5.0,
            "change": 7.0,
            "hint": 3.0
        },
        "gems": {
            "100": 0.99,
            "500": 4.99,
            "1000": 9.99,
            "5000": 49.99
        },
        "subscription": {
            "monthly": 9.99,
            "yearly": 99.99
        }
    }
    
    try:
        return prices[item_type][item_id] * quantity
    except KeyError:
        raise HTTPException(status_code=400, detail="Invalid item type or ID")

def apply_purchase_effects(user: User, purchase: PurchaseRequest):
    """Apply the effects of a purchase to the user"""
    if purchase.item_type == "gems":
        gem_amounts = {
            "100": 100,
            "500": 500,
            "1000": 1000,
            "5000": 5000
        }
        user.gems += gem_amounts[purchase.item_id] * purchase.quantity
    
    elif purchase.item_type == "lifeline":
        if purchase.item_id == "change":
            user.lifeline_changes_remaining += purchase.quantity
    
    elif purchase.item_type == "subscription":
        # Handle subscription logic
        pass
    else:
        raise HTTPException(status_code=400, detail="Invalid item type") 