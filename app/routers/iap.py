"""
IAP Router - In-App Purchase verification for Apple and Google
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from app.db import get_async_db
from app.models.user import User
from app.models.wallet import IapReceipt
from app.dependencies import get_current_user
from app.services.iap_service import (
    verify_apple_receipt,
    verify_google_purchase,
    get_product_credit_amount
)
from app.services.wallet_service import adjust_wallet_balance

router = APIRouter(prefix="/iap", tags=["IAP"])


class AppleVerifyRequest(BaseModel):
    receipt_data: str = Field(..., description="Base64-encoded receipt data")
    product_id: str = Field(..., description="Product ID from the receipt")
    environment: str = Field(default="production", pattern="^(production|sandbox)$")


class GoogleVerifyRequest(BaseModel):
    package_name: str = Field(..., description="Android app package name")
    product_id: str = Field(..., description="Product ID from the purchase")
    purchase_token: str = Field(..., description="Purchase token from Google Play")


class IapVerifyResponse(BaseModel):
    success: bool
    transaction_id: str
    product_id: str
    credited_amount_minor: Optional[int]
    credited_amount_usd: Optional[float]
    receipt_id: int


@router.post("/apple/verify", response_model=IapVerifyResponse)
async def verify_apple_purchase(
    request: AppleVerifyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Verify Apple receipt and credit wallet if valid.
    
    Handles idempotency - if receipt already verified, returns existing receipt.
    """
    # Verify receipt
    verification_result = await verify_apple_receipt(
        receipt_data=request.receipt_data,
        product_id=request.product_id,
        environment=request.environment
    )
    
    if not verification_result.get('verified'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Receipt verification failed: {verification_result.get('error', 'Unknown error')}"
        )
    
    transaction_id = verification_result.get('transaction_id')
    if not transaction_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Transaction ID not found in receipt"
        )
    
    # Check idempotency
    stmt = select(IapReceipt).where(
        and_(
            IapReceipt.platform == 'apple',
            IapReceipt.transaction_id == transaction_id
        )
    )
    result = await db.execute(stmt)
    existing_receipt = result.scalar_one_or_none()
    
    if existing_receipt:
        # Already processed
        return IapVerifyResponse(
            success=True,
            transaction_id=transaction_id,
            product_id=existing_receipt.product_id,
            credited_amount_minor=existing_receipt.credited_amount_minor,
            credited_amount_usd=existing_receipt.credited_amount_minor / 100.0 if existing_receipt.credited_amount_minor else None,
            receipt_id=existing_receipt.id
        )
    
    # Get credit amount for product from database
    credit_amount_minor = await get_product_credit_amount(db, request.product_id, platform='apple')
    if credit_amount_minor is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product ID {request.product_id} not found in product tables or price_minor is not set"
        )
    
    # Create receipt record
    receipt = IapReceipt(
        user_id=user.account_id,
        platform='apple',
        transaction_id=transaction_id,
        product_id=request.product_id,
        receipt_data=request.receipt_data,
        status='verified',
        credited_amount_minor=None,  # Will be set after wallet credit
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(receipt)
    await db.flush()
    
    # Credit wallet
    try:
        new_balance = await adjust_wallet_balance(
            db=db,
            user_id=user.account_id,
            currency='usd',
            delta_minor=credit_amount_minor,
            kind='deposit',
            external_ref_type='iap_apple',
            external_ref_id=transaction_id,
            livemode=False
        )
        
        receipt.credited_amount_minor = credit_amount_minor
        receipt.status = 'consumed'
        receipt.updated_at = datetime.utcnow()
        
        await db.commit()
        
        return IapVerifyResponse(
            success=True,
            transaction_id=transaction_id,
            product_id=request.product_id,
            credited_amount_minor=credit_amount_minor,
            credited_amount_usd=credit_amount_minor / 100.0,
            receipt_id=receipt.id
        )
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to credit wallet: {str(e)}"
        )


@router.post("/google/verify", response_model=IapVerifyResponse)
async def verify_google_purchase(
    request: GoogleVerifyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Verify Google Play purchase and credit wallet if valid.
    
    Handles idempotency - if purchase already verified, returns existing receipt.
    """
    # Verify purchase
    verification_result = await verify_google_purchase(
        package_name=request.package_name,
        product_id=request.product_id,
        purchase_token=request.purchase_token
    )
    
    if not verification_result.get('verified'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Purchase verification failed: {verification_result.get('error', 'Unknown error')}"
        )
    
    transaction_id = verification_result.get('transaction_id')
    if not transaction_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Transaction ID not found in purchase"
        )
    
    # Check idempotency
    stmt = select(IapReceipt).where(
        and_(
            IapReceipt.platform == 'google',
            IapReceipt.transaction_id == transaction_id
        )
    )
    result = await db.execute(stmt)
    existing_receipt = result.scalar_one_or_none()
    
    if existing_receipt:
        # Already processed
        return IapVerifyResponse(
            success=True,
            transaction_id=transaction_id,
            product_id=existing_receipt.product_id,
            credited_amount_minor=existing_receipt.credited_amount_minor,
            credited_amount_usd=existing_receipt.credited_amount_minor / 100.0 if existing_receipt.credited_amount_minor else None,
            receipt_id=existing_receipt.id
        )
    
    # Get credit amount for product from database
    credit_amount_minor = await get_product_credit_amount(db, request.product_id, platform='google')
    if credit_amount_minor is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product ID {request.product_id} not found in product tables or price_minor is not set"
        )
    
    # Create receipt record
    receipt = IapReceipt(
        user_id=user.account_id,
        platform='google',
        transaction_id=transaction_id,
        product_id=request.product_id,
        receipt_data=request.purchase_token,  # Store token as receipt data
        status='verified',
        credited_amount_minor=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(receipt)
    await db.flush()
    
    # Credit wallet
    try:
        new_balance = await adjust_wallet_balance(
            db=db,
            user_id=user.account_id,
            currency='usd',
            delta_minor=credit_amount_minor,
            kind='deposit',
            external_ref_type='iap_google',
            external_ref_id=transaction_id,
            livemode=False
        )
        
        receipt.credited_amount_minor = credit_amount_minor
        receipt.status = 'consumed'
        receipt.updated_at = datetime.utcnow()
        
        await db.commit()
        
        return IapVerifyResponse(
            success=True,
            transaction_id=transaction_id,
            product_id=request.product_id,
            credited_amount_minor=credit_amount_minor,
            credited_amount_usd=credit_amount_minor / 100.0,
            receipt_id=receipt.id
        )
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to credit wallet: {str(e)}"
        )


@router.post("/apple/webhook")
async def apple_webhook():
    """
    Apple Server-to-Server Notification (SSN) webhook endpoint.
    
    TODO: Implement Apple SSN webhook handling.
    See: https://developer.apple.com/documentation/appstoreservernotifications
    """
    # TODO: Implement Apple webhook handling
    return {"status": "not_implemented", "message": "Apple webhook not yet implemented"}


@router.post("/google/webhook")
async def google_webhook():
    """
    Google Play Real-time Developer Notifications (RTDN) webhook endpoint.
    
    TODO: Implement Google RTDN webhook handling.
    See: https://developer.android.com/google/play/billing/rtdn-reference
    """
    # TODO: Implement Google webhook handling
    return {"status": "not_implemented", "message": "Google webhook not yet implemented"}

