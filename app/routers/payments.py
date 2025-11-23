"""
Payments Router - Stripe PaymentIntent for wallet top-ups and product purchases
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, Literal
import os
import logging
from app.db import get_async_db
from app.models.user import User
from app.dependencies import get_current_user
from app.services.stripe_service import (
    get_publishable_key,
    get_or_create_stripe_customer_for_user,
    create_ephemeral_key_for_customer,
    create_payment_intent_for_topup,
    StripeError
)
from app.services.iap_service import get_product_credit_amount

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["Payments"])


class PaymentSheetInitRequest(BaseModel):
    """Request model for initializing Stripe PaymentSheet"""
    amount_minor: Optional[int] = Field(None, gt=0, description="Amount in minor units (cents) for wallet top-up")
    product_id: Optional[str] = Field(None, description="Product ID for product purchase (e.g., GP001, AV001)")
    topup_type: Literal["wallet_topup", "product"] = Field(..., description="Type of payment: wallet_topup or product")
    currency: Optional[str] = Field("usd", description="Currency code (default: usd)")


class PaymentConfigResponse(BaseModel):
    """Response model for payment configuration"""
    publishable_key: str
    currency: str


class PaymentSheetResponse(BaseModel):
    """Response model for PaymentSheet initialization"""
    customerId: str
    ephemeralKeySecret: str
    paymentIntentClientSecret: str
    amount_minor: int
    currency: str
    topup_type: str
    product_id: Optional[str] = None


@router.get("/config", response_model=PaymentConfigResponse)
async def get_payment_config(
    user: User = Depends(get_current_user)
):
    """
    Get payment configuration for frontend Stripe SDK initialization.
    
    Returns the publishable key and default currency needed to initialize
    Stripe PaymentSheet/PaymentElement in the frontend.
    
    **Test Cards:**
    - Use card number `4242 4242 4242 4242`, any future expiry date, any CVC, any ZIP
    - For 3DS testing: `4000 0027 6000 3184`
    - See Stripe test cards documentation for more: https://stripe.com/docs/testing
    
    **Apple Pay / Google Pay:**
    - Backend is compatible via `automatic_payment_methods.enabled=True`
    - Frontend needs to enable Apple Pay/Google Pay in Stripe Dashboard and Stripe SDK config
    """
    publishable_key = get_publishable_key()
    if not publishable_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe publishable key not configured"
        )
    
    currency = os.getenv("PAYMENTS_DEFAULT_CURRENCY", "usd")
    
    return PaymentConfigResponse(
        publishable_key=publishable_key,
        currency=currency
    )


@router.post("/payment-sheet", response_model=PaymentSheetResponse)
async def initialize_payment_sheet(
    request: PaymentSheetInitRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Initialize Stripe PaymentSheet for wallet top-up or product purchase.
    
    Returns customer ID, ephemeral key secret, and payment intent client secret
    needed by the frontend to open Stripe PaymentSheet with Card, Apple Pay, and Google Pay support.
    
    **Business Rules:**
    - For `wallet_topup`: Requires `amount_minor > 0`
    - For `product`: Requires `product_id`, amount is looked up from database (client cannot set price)
    
    **Frontend Usage:**
    1. Call this endpoint to get payment sheet configuration
    2. Initialize Stripe SDK with `publishable_key` from `/config`
    3. Use returned values to present PaymentSheet
    4. On successful payment, webhook will credit user's wallet
    
    **Test Cards:**
    - Use `4242 4242 4242 4242` for successful payment
    - Use `4000 0000 0000 0002` for card declined
    - See Stripe test cards: https://stripe.com/docs/testing
    
    **Webhook:**
    - Wallet is credited asynchronously via Stripe webhook on `payment_intent.succeeded`
    - Frontend should poll wallet balance or listen for wallet update events
    """
    # Validate request based on topup_type
    if request.topup_type == "wallet_topup":
        if not request.amount_minor or request.amount_minor <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="amount_minor is required and must be greater than 0 for wallet_topup"
            )
        amount_minor = request.amount_minor
        product_id = None
    elif request.topup_type == "product":
        if not request.product_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="product_id is required for product purchases"
            )
        # Look up product price from database (don't trust client)
        amount_minor = await get_product_credit_amount(db, request.product_id)
        if amount_minor is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product {request.product_id} not found or has no price"
            )
        product_id = request.product_id
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid topup_type: {request.topup_type}"
        )
    
    currency = request.currency or os.getenv("PAYMENTS_DEFAULT_CURRENCY", "usd")
    
    try:
        # Get or create Stripe customer
        customer_id = await get_or_create_stripe_customer_for_user(user, db)
        
        # Create ephemeral key
        ephemeral_key = create_ephemeral_key_for_customer(customer_id, stripe_api_version="2023-10-16")
        
        # Create payment intent
        payment_intent = create_payment_intent_for_topup(
            amount_minor=amount_minor,
            currency=currency,
            user=user,
            topup_type=request.topup_type,
            product_id=product_id
        )
        
        return PaymentSheetResponse(
            customerId=customer_id,
            ephemeralKeySecret=ephemeral_key.secret,
            paymentIntentClientSecret=payment_intent.client_secret,
            amount_minor=amount_minor,
            currency=currency,
            topup_type=request.topup_type,
            product_id=product_id
        )
        
    except StripeError as e:
        logger.error(f"Stripe error initializing payment sheet for user {user.account_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize payment sheet: {str(e)}"
        )

