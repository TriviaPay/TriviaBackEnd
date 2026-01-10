"""
IAP Router - In-App Purchase verification for Apple and Google
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

import config
from app.db import get_async_db
from app.dependencies import get_current_user
from app.models.user import User
from app.services.apple_iap_service import process_apple_iap
from app.services.google_iap_service import process_google_iap

router = APIRouter(prefix="/iap", tags=["IAP"])


class AppleVerifyRequest(BaseModel):
    receipt_data: str = Field(
        ..., description="Base64-encoded receipt data from StoreKit"
    )
    product_id: str = Field(
        ..., description="Product ID from the receipt (e.g., GP001, AV001)"
    )
    environment: Optional[Literal["sandbox", "production"]] = Field(
        default="production",
        description="Environment: 'sandbox' for testing, 'production' for live purchases",
    )


class GoogleVerifyRequest(BaseModel):
    package_name: Optional[str] = Field(
        default=None,
        description="Android app package name (defaults to GOOGLE_IAP_PACKAGE_NAME from config)",
    )
    product_id: str = Field(
        ..., description="Product ID from the purchase (e.g., GP001, AV001)"
    )
    purchase_token: str = Field(
        ..., description="Purchase token from Google Play Billing"
    )


class IapVerifyResponse(BaseModel):
    success: bool
    platform: str
    transaction_id: str
    product_id: str
    credited_amount_minor: Optional[int]
    credited_amount_usd: Optional[float]
    new_balance_minor: Optional[int]
    new_balance_usd: Optional[float]
    receipt_id: int
    already_processed: Optional[bool] = False


@router.post("/apple/verify", response_model=IapVerifyResponse)
async def verify_apple_purchase(
    request: AppleVerifyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Verify Apple receipt and credit wallet if valid.

    Uses Apple's verifyReceipt API to validate the receipt, then credits the user's wallet
    with the product price from the database.

    **Testing with iOS Sandbox:**
    1. Create a sandbox tester Apple ID in App Store Connect
    2. Sign out of your Apple ID on the test device
    3. Use StoreKit/react-native-iap with environment='sandbox'
    4. After purchase, call this endpoint with the base64 receipt data
    5. Expect wallet balance to increase by the product's price_minor

    **Idempotency:**
    - If the same receipt is verified multiple times, the wallet is only credited once
    - Returns existing receipt details if already processed

    **Product IDs:**
    - Must match a product in the database (avatars, frames, gem packages, or badges)
    - Format: AV001, FR001, GP001, BD001, etc.
    - Price is always looked up from the database, never trusted from client
    """
    result = await process_apple_iap(
        db=db,
        user=user,
        receipt_data=request.receipt_data,
        product_id=request.product_id,
        environment=request.environment or "production",
    )

    return IapVerifyResponse(
        success=result["success"],
        platform=result["platform"],
        transaction_id=result["transaction_id"],
        product_id=result["product_id"],
        credited_amount_minor=result["credited_amount_minor"],
        credited_amount_usd=(
            result["credited_amount_minor"] / 100.0
            if result["credited_amount_minor"]
            else None
        ),
        new_balance_minor=result["new_balance_minor"],
        new_balance_usd=(
            result["new_balance_minor"] / 100.0 if result["new_balance_minor"] else None
        ),
        receipt_id=result["receipt_id"],
        already_processed=result.get("already_processed", False),
    )


@router.post("/google/verify", response_model=IapVerifyResponse)
async def verify_google_purchase(
    request: GoogleVerifyRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Verify Google Play purchase and credit wallet if valid.

    Uses Google Play Developer API to validate the purchase token, then credits the user's wallet
    with the product price from the database.

    **Testing with Android:**
    1. Upload your app to Google Play Console (internal testing or closed testing track)
    2. Add test accounts in Google Play Console
    3. Purchase productId via Play Billing Library
    4. Send purchaseToken to this endpoint
    5. Expect wallet balance to increase by the product's price_minor

    **Idempotency:**
    - If the same purchase token is verified multiple times, the wallet is only credited once
    - Returns existing receipt details if already processed

    **Product IDs:**
    - Must match a product in the database (avatars, frames, gem packages, or badges)
    - Format: AV001, FR001, GP001, BD001, etc.
    - Price is always looked up from the database, never trusted from client

    **Package Name:**
    - Defaults to GOOGLE_IAP_PACKAGE_NAME from environment variables
    - Can be overridden in the request if needed
    """
    package_name = request.package_name or config.GOOGLE_IAP_PACKAGE_NAME

    if not package_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="package_name is required (either in request or GOOGLE_IAP_PACKAGE_NAME env var)",
        )

    result = await process_google_iap(
        db=db,
        user=user,
        package_name=package_name,
        product_id=request.product_id,
        purchase_token=request.purchase_token,
    )

    return IapVerifyResponse(
        success=result["success"],
        platform=result["platform"],
        transaction_id=result["transaction_id"],
        product_id=result["product_id"],
        credited_amount_minor=result["credited_amount_minor"],
        credited_amount_usd=(
            result["credited_amount_minor"] / 100.0
            if result["credited_amount_minor"]
            else None
        ),
        new_balance_minor=result["new_balance_minor"],
        new_balance_usd=(
            result["new_balance_minor"] / 100.0 if result["new_balance_minor"] else None
        ),
        receipt_id=result["receipt_id"],
        already_processed=result.get("already_processed", False),
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
    return {
        "status": "not_implemented",
        "message": "Google webhook not yet implemented",
    }
