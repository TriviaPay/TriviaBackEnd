"""Payments/Wallet/IAP service layer."""

import base64
import json
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError

import core.config as config
from app.models.wallet import IapEvent, IapReceipt
from app.services.apple_iap_service import process_apple_iap, verify_signed_transaction_info
from app.services.google_iap_service import process_google_iap
from app.services.wallet_service import (
    adjust_wallet_balance,
    get_wallet_balance as wallet_service_get_wallet_balance,
)

from . import repository as payments_repository
from .schemas import (
    AppleVerifyRequest,
    GoogleVerifyRequest,
    IapVerifyResponse,
    WalletBalanceResponse,
    WalletTransactionResponse,
)


async def get_wallet_info(db, *, user, include_transactions: bool):
    currency = user.wallet_currency or "usd"
    balance_minor = await wallet_service_get_wallet_balance(db, user.account_id, currency)

    recent_transactions = None
    if include_transactions:
        transactions = await payments_repository.list_recent_wallet_transactions(
            db, user_id=user.account_id, limit=10
        )
        recent_transactions = [
            WalletTransactionResponse(
                id=t.id,
                amount_minor=t.amount_minor,
                amount_usd=t.amount_minor / 100.0,
                currency=t.currency,
                kind=t.kind,
                created_at=t.created_at.isoformat() if t.created_at else None,
            )
            for t in transactions
        ]

    return WalletBalanceResponse(
        balance_minor=balance_minor,
        balance_usd=balance_minor / 100.0,
        currency=currency,
        recent_transactions=recent_transactions,
    )


async def verify_apple_purchase(db, *, user, request: AppleVerifyRequest) -> IapVerifyResponse:
    result = await process_apple_iap(
        db=db,
        user=user,
        signed_transaction_info=request.signed_transaction_info,
        product_id=request.product_id,
        environment=request.environment or "production",
        app_account_token=request.app_account_token,
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


async def verify_google_purchase(db, *, user, request: GoogleVerifyRequest) -> IapVerifyResponse:
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


async def process_apple_notification(db, *, signed_payload: str):
    payload = verify_signed_transaction_info(signed_payload)
    notification_type = payload.get("notificationType")
    subtype = payload.get("subtype")
    event_id = payload.get("notificationUUID") or payload.get("notificationId")
    data = payload.get("data", {}) if isinstance(payload.get("data", {}), dict) else {}
    signed_tx = data.get("signedTransactionInfo")
    tx_payload = verify_signed_transaction_info(signed_tx) if signed_tx else {}
    transaction_id = tx_payload.get("transactionId")

    if not event_id:
        event_id = f"apple:{transaction_id or 'unknown'}:{notification_type or 'unknown'}"

    event = IapEvent(
        platform="apple",
        event_id=event_id,
        notification_type=notification_type,
        subtype=subtype,
        transaction_id=transaction_id,
        status="received",
        raw_payload=signed_payload,
        received_at=datetime.utcnow(),
    )

    try:
        db.add(event)
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return {"status": "already_processed", "event_id": event_id}

    if notification_type in ("REFUND", "REVOKE"):
        receipt_stmt = select(IapReceipt).where(
            and_(
                IapReceipt.platform == "apple",
                IapReceipt.transaction_id == transaction_id,
            )
        )
        receipt_result = await db.execute(receipt_stmt)
        receipt = receipt_result.scalar_one_or_none()
        if receipt:
            if receipt.status == "credited" and receipt.credited_amount_minor:
                await adjust_wallet_balance(
                    db=db,
                    user_id=receipt.user_id,
                    currency="usd",
                    delta_minor=-abs(int(receipt.credited_amount_minor)),
                    kind="iap_refund",
                    external_ref_type="iap_receipt_refund",
                    external_ref_id=str(receipt.id),
                    event_id=f"apple_refund:{transaction_id}",
                    livemode=(receipt.environment == "production"),
                )
            if receipt.status != "revoked":
                receipt.status = "revoked"
                receipt.revocation_date = datetime.now(timezone.utc)
                receipt.revocation_reason = notification_type
                receipt.updated_at = datetime.utcnow()

    event.status = "processed"
    event.processed_at = datetime.utcnow()
    await db.commit()
    return {"status": "processed", "event_id": event_id}


async def process_google_notification(db, *, payload: dict):
    message = payload.get("message", {}) if isinstance(payload, dict) else {}
    event_id = message.get("messageId") or payload.get("eventId")
    data_b64 = message.get("data")

    raw_payload = json.dumps(payload)
    decoded = {}
    if data_b64:
        try:
            decoded = json.loads(base64.b64decode(data_b64).decode("utf-8"))
        except Exception:
            decoded = {}

    one_time = decoded.get("oneTimeProductNotification", {})
    notification_type = one_time.get("notificationType")
    try:
        notification_type = int(notification_type) if notification_type is not None else None
    except (TypeError, ValueError):
        notification_type = None
    purchase_token = one_time.get("purchaseToken")
    product_id = one_time.get("sku")

    if not event_id:
        event_id = f"google:{purchase_token or 'unknown'}:{notification_type or 'unknown'}"

    event = IapEvent(
        platform="google",
        event_id=event_id,
        notification_type=str(notification_type) if notification_type is not None else None,
        transaction_id=None,
        purchase_token=purchase_token,
        status="received",
        raw_payload=raw_payload,
        received_at=datetime.utcnow(),
    )

    try:
        db.add(event)
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return {"status": "already_processed", "event_id": event_id}

    refund_types = set(config.GOOGLE_IAP_REFUND_NOTIFICATION_TYPES)
    if notification_type is not None and notification_type in refund_types and purchase_token:
        receipt_stmt = select(IapReceipt).where(
            and_(
                IapReceipt.platform == "google",
                IapReceipt.purchase_token == purchase_token,
            )
        )
        receipt_result = await db.execute(receipt_stmt)
        receipt = receipt_result.scalar_one_or_none()
        if receipt:
            if receipt.status == "credited" and receipt.credited_amount_minor:
                await adjust_wallet_balance(
                    db=db,
                    user_id=receipt.user_id,
                    currency="usd",
                    delta_minor=-abs(int(receipt.credited_amount_minor)),
                    kind="iap_refund",
                    external_ref_type="iap_receipt_refund",
                    external_ref_id=str(receipt.id),
                    event_id=f"google_refund:{purchase_token}",
                    livemode=True,
                )
            if receipt.status != "revoked":
                receipt.status = "revoked"
                receipt.revocation_date = datetime.now(timezone.utc)
                receipt.revocation_reason = str(notification_type)
                receipt.updated_at = datetime.utcnow()

    event.status = "processed"
    event.processed_at = datetime.utcnow()
    await db.commit()
    return {"status": "processed", "event_id": event_id, "product_id": product_id}


# --- Internal APIs for other domains ---


async def credit_wallet(db, *, account_id: int, amount_minor: int, reason: str):
    currency = await payments_repository.get_user_wallet_currency(db, user_id=account_id)
    external_ref_id = (reason or "credit")[:128]
    return await adjust_wallet_balance(
        db=db,
        user_id=account_id,
        currency=currency,
        delta_minor=abs(int(amount_minor)),
        kind="adjustment",
        external_ref_type="internal_credit",
        external_ref_id=external_ref_id,
        livemode=False,
    )


async def debit_wallet(db, *, account_id: int, amount_minor: int, reason: str):
    currency = await payments_repository.get_user_wallet_currency(db, user_id=account_id)
    external_ref_id = (reason or "debit")[:128]
    return await adjust_wallet_balance(
        db=db,
        user_id=account_id,
        currency=currency,
        delta_minor=-abs(int(amount_minor)),
        kind="adjustment",
        external_ref_type="internal_debit",
        external_ref_id=external_ref_id,
        livemode=False,
    )


async def get_wallet_balance(db, *, account_id: int) -> int:
    currency = await payments_repository.get_user_wallet_currency(db, user_id=account_id)
    return await wallet_service_get_wallet_balance(db, account_id, currency)
