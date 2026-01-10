"""
Apple IAP Service - Handles Apple App Store receipt verification
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Tuple

import httpx
from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

import config
from app.models.user import User
from app.models.wallet import IapReceipt
from app.services.product_pricing import get_price_minor_for_product_id
from app.services.wallet_service import adjust_wallet_balance

logger = logging.getLogger(__name__)

# Apple receipt verification endpoints
APPLE_PRODUCTION_URL = "https://buy.itunes.apple.com/verifyReceipt"
APPLE_SANDBOX_URL = "https://sandbox.itunes.apple.com/verifyReceipt"

# Apple status codes
APPLE_STATUS_OK = 0
APPLE_STATUS_SANDBOX_RECEIPT = 21007  # Sandbox receipt sent to production


async def verify_apple_receipt_with_apple_server(
    receipt_data: str,
    shared_secret: str,
    use_sandbox: bool,
) -> Dict[str, Any]:
    """
    Call Apple's verifyReceipt endpoint.

    Args:
        receipt_data: Base64-encoded receipt data
        shared_secret: App-specific shared secret from App Store Connect
        use_sandbox: Whether to use sandbox endpoint

    Returns:
        Parsed JSON response from Apple

    Raises:
        HTTPException(502, ...) if Apple cannot be reached or payload invalid
    """
    if not shared_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Apple IAP shared secret not configured",
        )

    url = APPLE_SANDBOX_URL if use_sandbox else APPLE_PRODUCTION_URL

    payload = {
        "receipt-data": receipt_data,
        "password": shared_secret,
        "exclude-old-transactions": True,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()

            # If we called production and got status 21007 (sandbox receipt), retry with sandbox
            if not use_sandbox and result.get("status") == APPLE_STATUS_SANDBOX_RECEIPT:
                logger.info(
                    "Received sandbox receipt in production, retrying with sandbox endpoint"
                )
                async with httpx.AsyncClient(timeout=30.0) as sandbox_client:
                    sandbox_response = await sandbox_client.post(
                        APPLE_SANDBOX_URL, json=payload
                    )
                    sandbox_response.raise_for_status()
                    return sandbox_response.json()

            return result

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Timeout connecting to Apple receipt verification service",
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Apple receipt verification service returned error: {e.response.status_code}",
        )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to connect to Apple receipt verification service: {str(e)}",
        )
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Invalid JSON response from Apple receipt verification service",
        )


async def extract_latest_apple_transaction(
    response: Dict[str, Any],
    expected_product_id: str,
) -> Tuple[str, str, int]:
    """
    Given Apple's verifyReceipt response, extract:
    - transaction_id (original_transaction_id or transaction_id)
    - product_id
    - purchase_timestamp (purchase_date_ms)

    Args:
        response: Apple verifyReceipt response JSON
        expected_product_id: Expected product ID to match

    Returns:
        Tuple of (transaction_id, product_id, purchase_date_ms)

    Raises:
        HTTPException(400, ...) if receipt is invalid, product mismatch, or malformed
    """
    status_code = response.get("status")
    if status_code != APPLE_STATUS_OK:
        error_messages = {
            21000: "The App Store could not read the receipt data",
            21002: "The receipt data was malformed",
            21003: "The receipt could not be authenticated",
            21004: "The shared secret does not match",
            21005: "The receipt server is temporarily unavailable",
            21006: "This receipt is valid but the subscription has expired",
            21007: "This receipt is from the sandbox environment",
            21008: "This receipt is from the production environment",
            21010: "This receipt could not be authorized",
        }
        error_msg = error_messages.get(
            status_code, f"Receipt verification failed with status {status_code}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid receipt: {error_msg}",
        )

    # Try to get latest_receipt_info first (for subscription receipts)
    latest_receipt_info = response.get("latest_receipt_info", [])

    # If not available, try receipt.in_app (for one-time purchases)
    if not latest_receipt_info:
        receipt = response.get("receipt", {})
        latest_receipt_info = receipt.get("in_app", [])

    if not latest_receipt_info:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No transaction information found in receipt",
        )

    # Find the most recent transaction matching expected_product_id
    matching_transactions = [
        tx for tx in latest_receipt_info if tx.get("product_id") == expected_product_id
    ]

    if not matching_transactions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product ID '{expected_product_id}' not found in receipt. Found products: {[tx.get('product_id') for tx in latest_receipt_info]}",
        )

    # Get the most recent transaction (by purchase_date_ms)
    latest_transaction = max(
        matching_transactions, key=lambda tx: int(tx.get("purchase_date_ms", 0))
    )

    # Use original_transaction_id if available (for subscriptions), otherwise transaction_id
    transaction_id = latest_transaction.get(
        "original_transaction_id"
    ) or latest_transaction.get("transaction_id")
    product_id = latest_transaction.get("product_id")
    purchase_date_ms = int(latest_transaction.get("purchase_date_ms", 0))

    if not transaction_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Transaction ID not found in receipt",
        )

    if product_id != expected_product_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product ID mismatch: expected '{expected_product_id}', got '{product_id}'",
        )

    return (transaction_id, product_id, purchase_date_ms)


async def process_apple_iap(
    db: AsyncSession,
    user: User,
    receipt_data: str,
    product_id: str,
    environment: str,  # 'sandbox' or 'production'
) -> Dict[str, Any]:
    """
    High-level Apple IAP verification and wallet crediting.

    Args:
        db: Async database session
        user: User object
        receipt_data: Base64-encoded receipt data
        product_id: Expected product ID
        environment: 'sandbox' or 'production'

    Returns:
        Dict with success status, transaction details, and new balance
    """
    # Determine whether to call sandbox or production
    use_sandbox = (environment == "sandbox") or config.APPLE_IAP_USE_SANDBOX

    # Get shared secret from config
    shared_secret = config.APPLE_IAP_SHARED_SECRET
    if not shared_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Apple IAP shared secret not configured",
        )

    # Call Apple verification
    try:
        apple_response = await verify_apple_receipt_with_apple_server(
            receipt_data=receipt_data,
            shared_secret=shared_secret,
            use_sandbox=use_sandbox,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during Apple receipt verification: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to verify receipt with Apple: {str(e)}",
        )

    # Extract transaction details
    try:
        transaction_id, confirmed_product_id, purchase_date_ms = (
            await extract_latest_apple_transaction(
                response=apple_response, expected_product_id=product_id
            )
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error extracting transaction: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse receipt: {str(e)}",
        )

    # Check idempotency - see if we already processed this transaction
    stmt = select(IapReceipt).where(
        and_(
            IapReceipt.platform == "apple", IapReceipt.transaction_id == transaction_id
        )
    )
    result = await db.execute(stmt)
    existing_receipt = result.scalar_one_or_none()

    if existing_receipt:
        if existing_receipt.status in ("verified", "consumed"):
            # Already processed, return existing result
            logger.info(f"Apple IAP transaction {transaction_id} already processed")
            # Get current balance
            user_stmt = select(User).where(User.account_id == user.account_id)
            user_result = await db.execute(user_stmt)
            current_user = user_result.scalar_one_or_none()
            current_balance = current_user.wallet_balance_minor if current_user else 0

            return {
                "success": True,
                "platform": "apple",
                "transaction_id": transaction_id,
                "product_id": confirmed_product_id,
                "credited_amount_minor": existing_receipt.credited_amount_minor,
                "new_balance_minor": current_balance,
                "receipt_id": existing_receipt.id,
                "already_processed": True,
            }
        elif existing_receipt.status == "failed":
            # Previously failed, but we'll try again (might have been a transient error)
            logger.warning(
                f"Apple IAP transaction {transaction_id} previously failed, retrying"
            )

    # Look up price from database
    try:
        price_minor = await get_price_minor_for_product_id(db, product_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get price for product {product_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product ID '{product_id}' not found or invalid",
        )

    # Insert or update iap_receipts row
    if existing_receipt:
        receipt = existing_receipt
        receipt.status = "verified"
        receipt.credited_amount_minor = price_minor
        receipt.updated_at = datetime.utcnow()
    else:
        receipt = IapReceipt(
            user_id=user.account_id,
            platform="apple",
            transaction_id=transaction_id,
            product_id=confirmed_product_id,
            receipt_data=receipt_data,
            status="verified",
            credited_amount_minor=price_minor,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(receipt)

    await db.flush()

    # Credit wallet
    try:
        new_balance = await adjust_wallet_balance(
            db=db,
            user_id=user.account_id,
            currency="usd",
            delta_minor=price_minor,
            kind="deposit",
            external_ref_type="iap_receipt",
            external_ref_id=str(receipt.id),
            event_id=f"apple:{transaction_id}",
            livemode=(environment == "production"),
        )

        # Mark receipt as consumed
        receipt.status = "consumed"
        receipt.updated_at = datetime.utcnow()
        await db.commit()

        logger.info(
            f"Apple IAP processed: user={user.account_id}, product={product_id}, "
            f"transaction={transaction_id}, amount={price_minor}, balance={new_balance}"
        )

        return {
            "success": True,
            "platform": "apple",
            "transaction_id": transaction_id,
            "product_id": confirmed_product_id,
            "credited_amount_minor": price_minor,
            "new_balance_minor": new_balance,
            "receipt_id": receipt.id,
            "already_processed": False,
        }

    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to credit wallet for Apple IAP: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to credit wallet: {str(e)}",
        )
