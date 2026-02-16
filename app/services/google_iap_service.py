"""
Google IAP Service - Handles Google Play purchase verification
"""

import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict

import google.auth
from fastapi import HTTPException, status
from google.oauth2 import service_account
from googleapiclient import discovery
from googleapiclient.errors import HttpError
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import core.config as config
from app.models.user import User
from app.models.wallet import IapEvent, IapReceipt
from app.services.product_pricing import get_product_info
from app.services.wallet_service import adjust_wallet_balance

logger = logging.getLogger(__name__)

# Google Play purchase states
GOOGLE_PURCHASE_STATE_PURCHASED = 0
GOOGLE_PURCHASE_STATE_CANCELLED = 1
GOOGLE_PURCHASE_STATE_PENDING = 2

# Thread pool for Google API calls (sync API wrapped in async)
_executor = ThreadPoolExecutor(max_workers=5)


def get_google_credentials_from_env() -> google.auth.credentials.Credentials:
    """
    Load Google service account credentials from environment variable.

    GOOGLE_IAP_SERVICE_ACCOUNT_JSON can be either:
    - A path to a JSON key file, or
    - The raw JSON content

    Returns:
        google.oauth2.service_account.Credentials with androidpublisher scope

    Raises:
        HTTPException(500, ...) if credentials cannot be loaded
    """
    service_account_json = config.GOOGLE_IAP_SERVICE_ACCOUNT_JSON

    if not service_account_json:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google IAP service account JSON not configured",
        )

    try:
        # Check if it's a file path
        if os.path.exists(service_account_json):
            credentials = service_account.Credentials.from_service_account_file(
                service_account_json,
                scopes=["https://www.googleapis.com/auth/androidpublisher"],
            )
        else:
            # Assume it's raw JSON content
            try:
                service_account_info = json.loads(service_account_json)
            except json.JSONDecodeError:
                raise ValueError("GOOGLE_IAP_SERVICE_ACCOUNT_JSON is not valid JSON")

            credentials = service_account.Credentials.from_service_account_info(
                service_account_info,
                scopes=["https://www.googleapis.com/auth/androidpublisher"],
            )

        return credentials

    except Exception as e:
        logger.error(f"Failed to load Google service account credentials: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load Google service account credentials: {str(e)}",
        )


def get_android_publisher_client(credentials) -> Any:
    """
    Create a Google AndroidPublisher API client.

    Args:
        credentials: Google service account credentials

    Returns:
        Service object with .purchases().products() access
    """
    try:
        service = discovery.build(
            "androidpublisher", "v3", credentials=credentials, cache_discovery=False
        )
        return service
    except Exception as e:
        logger.error(f"Failed to create Android Publisher client: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create Android Publisher client: {str(e)}",
        )


async def acknowledge_google_purchase(
    package_name: str, product_id: str, purchase_token: str
) -> None:
    credentials = get_google_credentials_from_env()
    service = get_android_publisher_client(credentials)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        _executor,
        lambda: service.purchases()
        .products()
        .acknowledge(
            packageName=package_name,
            productId=product_id,
            token=purchase_token,
            body={},
        )
        .execute(),
    )


async def consume_google_purchase(
    package_name: str, product_id: str, purchase_token: str
) -> None:
    credentials = get_google_credentials_from_env()
    service = get_android_publisher_client(credentials)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        _executor,
        lambda: service.purchases()
        .products()
        .consume(
            packageName=package_name,
            productId=product_id,
            token=purchase_token,
            body={},
        )
        .execute(),
    )


async def verify_google_purchase_token(
    package_name: str,
    product_id: str,
    purchase_token: str,
) -> Dict[str, Any]:
    """
    Verify Google Play purchase token using Android Publisher API.

    Args:
        package_name: Android app package name
        product_id: Product ID from the purchase
        purchase_token: Purchase token from Google Play

    Returns:
        Raw response JSON from Google Play API

    Raises:
        HTTPException(400, ...) if purchase is invalid
        HTTPException(502, ...) if Google API call fails
    """
    try:
        credentials = get_google_credentials_from_env()
        service = get_android_publisher_client(credentials)

        # Wrap sync Google API call in async executor
        loop = asyncio.get_event_loop()
        purchase = await loop.run_in_executor(
            _executor,
            lambda: service.purchases()
            .products()
            .get(packageName=package_name, productId=product_id, token=purchase_token)
            .execute(),
        )

        # Validate purchase state
        purchase_state = purchase.get("purchaseState")
        if purchase_state != GOOGLE_PURCHASE_STATE_PURCHASED:
            state_names = {
                GOOGLE_PURCHASE_STATE_PURCHASED: "purchased",
                GOOGLE_PURCHASE_STATE_CANCELLED: "cancelled",
                GOOGLE_PURCHASE_STATE_PENDING: "pending",
            }
            state_name = state_names.get(purchase_state, f"unknown({purchase_state})")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Purchase is not in purchased state: {state_name}",
            )

        return purchase

    except HttpError as e:
        error_details = json.loads(e.content.decode("utf-8")) if e.content else {}
        error_reason = error_details.get("error", {}).get("message", str(e))

        if e.resp.status == 404:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Purchase not found: {error_reason}",
            )
        elif e.resp.status == 401:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Google API authentication failed: {error_reason}",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Google Play API error: {error_reason}",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error verifying Google purchase: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to verify purchase with Google: {str(e)}",
        )


async def process_google_iap(
    db: AsyncSession,
    user: User,
    package_name: str,
    product_id: str,
    purchase_token: str,
) -> Dict[str, Any]:
    """
    High-level Google IAP verification and wallet crediting.

    Args:
        db: Async database session
        user: User object
        package_name: Android app package name
        product_id: Product ID from the purchase
        purchase_token: Purchase token from Google Play

    Returns:
        Dict with success status, transaction details, and new balance
    """
    user_id = user.account_id

    # Block if a refund/chargeback event was already received
    refund_types = {str(t) for t in config.GOOGLE_IAP_REFUND_NOTIFICATION_TYPES}
    event_stmt = (
        select(IapEvent)
        .where(
            and_(
                IapEvent.platform == "google",
                IapEvent.purchase_token == purchase_token,
                IapEvent.notification_type.in_(refund_types),
            )
        )
        .limit(1)
    )
    event_result = await db.execute(event_stmt)
    refund_event = event_result.scalar_one_or_none()
    if refund_event:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Transaction has been revoked",
        )

    # Verify purchase with Google
    try:
        google_response = await verify_google_purchase_token(
            package_name=package_name,
            product_id=product_id,
            purchase_token=purchase_token,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during Google purchase verification: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to verify purchase with Google: {str(e)}",
        )

    # Extract transaction details
    # Use orderId as transaction_id (most stable identifier)
    # Fallback to productId + purchaseToken combination if orderId not available
    transaction_id = google_response.get("orderId")
    if not transaction_id:
        # Fallback: use productId + purchaseToken as unique identifier
        transaction_id = f"{product_id}:{purchase_token[:20]}"

    confirmed_product_id = google_response.get("productId", product_id)
    if confirmed_product_id != product_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product ID mismatch: expected '{product_id}', got '{confirmed_product_id}'",
        )

    # Check idempotency - see if we already processed this transaction
    stmt = (
        select(IapReceipt)
        .where(
            and_(
                IapReceipt.platform == "google",
                IapReceipt.purchase_token == purchase_token,
            )
        )
        .with_for_update()
    )
    result = await db.execute(stmt)
    existing_receipt = result.scalar_one_or_none()
    if not existing_receipt:
        legacy_stmt = (
            select(IapReceipt)
            .where(
                and_(
                    IapReceipt.platform == "google",
                    IapReceipt.transaction_id == transaction_id,
                )
            )
            .with_for_update()
        )
        legacy_result = await db.execute(legacy_stmt)
        existing_receipt = legacy_result.scalar_one_or_none()

    if existing_receipt:
        if existing_receipt.status in ("credited", "consumed"):
            # Already processed, return existing result
            logger.info(f"Google IAP transaction {transaction_id} already processed")
            # Get current balance
            user_stmt = select(User).where(User.account_id == user_id)
            user_result = await db.execute(user_stmt)
            current_user = user_result.scalar_one_or_none()
            current_balance = current_user.wallet_balance_minor if current_user else 0

            return {
                "success": True,
                "platform": "google",
                "transaction_id": transaction_id,
                "product_id": confirmed_product_id,
                "credited_amount_minor": existing_receipt.credited_amount_minor,
                "new_balance_minor": current_balance,
                "receipt_id": existing_receipt.id,
                "already_processed": True,
            }
        elif existing_receipt.status == "revoked":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Transaction has been revoked",
            )
        elif existing_receipt.status == "failed":
            # Previously failed, but we'll try again
            logger.warning(
                f"Google IAP transaction {transaction_id} previously failed, retrying"
            )

    # Look up product from database
    try:
        product_info = await get_product_info(db, product_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get product for {product_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product ID '{product_id}' not found or invalid",
        )

    price_minor = product_info["price_minor"]
    product_type = product_info["product_type"]
    if product_type == "subscription":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subscription purchases are not supported in this endpoint",
        )

    purchase_state = google_response.get("purchaseState")
    acknowledgement_state = google_response.get("acknowledgementState")
    purchase_time_ms = google_response.get("purchaseTimeMillis")

    try:
        # Insert or update iap_receipts row
        if existing_receipt:
            receipt = existing_receipt
            receipt.status = "verified"
            receipt.credited_amount_minor = price_minor
            receipt.updated_at = datetime.utcnow()
            receipt.purchase_state = purchase_state
            receipt.acknowledgement_state = acknowledgement_state
            receipt.purchase_time_ms = int(purchase_time_ms) if purchase_time_ms else None
            if not receipt.purchase_token:
                receipt.purchase_token = purchase_token
        else:
            receipt = IapReceipt(
                user_id=user_id,
                platform="google",
                transaction_id=transaction_id,
                product_id=confirmed_product_id,
                product_type=product_type,
                receipt_data=purchase_token,
                purchase_token=purchase_token,
                status="verified",
                credited_amount_minor=price_minor,
                purchase_state=purchase_state,
                acknowledgement_state=acknowledgement_state,
                purchase_time_ms=int(purchase_time_ms) if purchase_time_ms else None,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(receipt)

        await db.flush()

        # Credit wallet
        new_balance = await adjust_wallet_balance(
            db=db,
            user_id=user_id,
            currency="usd",
            delta_minor=price_minor,
            kind="deposit",
            external_ref_type="iap_receipt",
            external_ref_id=str(receipt.id),
            event_id=f"google:{transaction_id}",
            livemode=True,  # Google Play purchases are always live
        )

        receipt.status = "credited"
        receipt.updated_at = datetime.utcnow()

        # Acknowledge or consume after crediting
        try:
            if product_type == "consumable":
                await consume_google_purchase(package_name, product_id, purchase_token)
            else:
                if acknowledgement_state != 1:
                    await acknowledge_google_purchase(
                        package_name, product_id, purchase_token
                    )
        except Exception as exc:
            logger.error(f"Failed to acknowledge/consume Google purchase: {exc}")

        await db.commit()

        logger.info(
            f"Google IAP processed: user={user_id}, product={product_id}, "
            f"transaction={transaction_id}, amount={price_minor}, balance={new_balance}"
        )

        return {
            "success": True,
            "platform": "google",
            "transaction_id": transaction_id,
            "product_id": confirmed_product_id,
            "credited_amount_minor": price_minor,
            "new_balance_minor": new_balance,
            "receipt_id": receipt.id,
            "already_processed": False,
        }

    except IntegrityError:
        await db.rollback()
        existing = None
        for _ in range(5):
            existing_stmt = select(IapReceipt).where(
                and_(
                    IapReceipt.platform == "google",
                    IapReceipt.purchase_token == purchase_token,
                )
            )
            existing_result = await db.execute(existing_stmt)
            existing = existing_result.scalar_one_or_none()
            if existing:
                break
            await asyncio.sleep(0.05)

        if existing:
            user_stmt = select(User).where(User.account_id == user_id)
            user_result = await db.execute(user_stmt)
            current_user = user_result.scalar_one_or_none()
            current_balance = current_user.wallet_balance_minor if current_user else 0
            return {
                "success": True,
                "platform": "google",
                "transaction_id": transaction_id,
                "product_id": confirmed_product_id,
                "credited_amount_minor": existing.credited_amount_minor,
                "new_balance_minor": current_balance,
                "receipt_id": existing.id,
                "already_processed": True,
            }
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to credit wallet for Google IAP: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to credit wallet: {str(e)}",
        )
