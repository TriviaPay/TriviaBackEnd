"""Payments/Wallet/IAP service layer."""

from __future__ import annotations

import base64
from typing import Optional
import json
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError

import core.config as config
from app.models.wallet import IapEvent, IapReceipt
import logging

from app.services.apple_iap_service import process_apple_iap, verify_signed_transaction_info
from app.services.google_iap_service import get_google_subscription_state, process_google_iap
from app.services.subscription_iap_service import (
    activate_subscription_from_iap,
    deactivate_subscription,
    lookup_subscription_plan,
    update_subscription_renewal_status,
)
from app.services.wallet_service import (
    adjust_wallet_balance,
    get_wallet_balance as wallet_service_get_wallet_balance,
)

from . import repository as payments_repository
from .schemas import (
    AppleVerifyRequest,
    GoogleVerifyRequest,
    IapVerifyResponse,
    SubscriptionInfo,
    WalletBalanceResponse,
    WalletTransactionResponse,
)

logger = logging.getLogger(__name__)


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

    # Check if this product maps to a subscription plan
    subscription_info = None
    if result["success"] and not result.get("already_processed"):
        subscription_info = await _try_activate_subscription(
            db, platform="apple", product_id=request.product_id,
            user_id=user.account_id, receipt_id=result["receipt_id"],
            livemode=(request.environment != "sandbox"),
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
        subscription=subscription_info,
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

    # Check if this product maps to a subscription plan
    subscription_info = None
    if result["success"] and not result.get("already_processed"):
        subscription_info = await _try_activate_subscription(
            db, platform="google", product_id=request.product_id,
            user_id=user.account_id, receipt_id=result["receipt_id"],
            livemode=True,
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
        subscription=subscription_info,
    )


async def _try_activate_subscription(
    db, *, platform: str, product_id: str, user_id: int, receipt_id: int, livemode: bool
) -> SubscriptionInfo | None:
    """If the product_id maps to a subscription plan, activate it."""
    plan = await lookup_subscription_plan(db, platform=platform, product_id=product_id)
    if not plan:
        return None

    try:
        sub_result = await activate_subscription_from_iap(
            db, user_id=user_id, plan=plan, receipt_id=receipt_id, livemode=livemode,
        )
        await db.commit()
        return SubscriptionInfo(**sub_result)
    except Exception as exc:
        logger.error("Failed to activate subscription for user=%s plan=%s: %s", user_id, plan.id, exc)
        return None


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
        received_at=datetime.now(timezone.utc),
    )

    try:
        db.add(event)
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return {"status": "already_processed", "event_id": event_id}

    # Handle subscription renewal notifications
    if notification_type == "DID_RENEW" and transaction_id:
        product_id = tx_payload.get("productId")
        original_tx_id = tx_payload.get("originalTransactionId")
        if product_id and original_tx_id:
            plan = await lookup_subscription_plan(db, platform="apple", product_id=product_id)
            if plan:
                # Find user from existing receipt using originalTransactionId
                receipt_stmt = select(IapReceipt).where(
                    and_(
                        IapReceipt.platform == "apple",
                        IapReceipt.original_transaction_id == original_tx_id,
                    )
                ).order_by(IapReceipt.created_at.desc()).limit(1)
                receipt_result = await db.execute(receipt_stmt)
                receipt = receipt_result.scalar_one_or_none()
                if receipt:
                    try:
                        await activate_subscription_from_iap(
                            db, user_id=receipt.user_id, plan=plan,
                            receipt_id=receipt.id, livemode=(receipt.environment == "production"),
                        )
                    except Exception as exc:
                        logger.error("Failed to renew subscription from Apple webhook: %s", exc)

    # Handle subscription expiry/revoke
    if notification_type in ("EXPIRED", "REVOKE") and transaction_id:
        product_id = tx_payload.get("productId")
        original_tx_id = tx_payload.get("originalTransactionId")
        if product_id and original_tx_id:
            plan = await lookup_subscription_plan(db, platform="apple", product_id=product_id)
            if plan:
                receipt_stmt = select(IapReceipt).where(
                    and_(
                        IapReceipt.platform == "apple",
                        IapReceipt.original_transaction_id == original_tx_id,
                    )
                ).order_by(IapReceipt.created_at.desc()).limit(1)
                receipt_result = await db.execute(receipt_stmt)
                receipt = receipt_result.scalar_one_or_none()
                if receipt:
                    new_status = "expired" if notification_type == "EXPIRED" else "revoked"
                    try:
                        await deactivate_subscription(
                            db, user_id=receipt.user_id, plan=plan, new_status=new_status,
                        )
                    except Exception as exc:
                        logger.error("Failed to deactivate subscription from Apple webhook: %s", exc)

    # Handle grace period expiry
    if notification_type == "GRACE_PERIOD_EXPIRED" and transaction_id:
        product_id = tx_payload.get("productId")
        original_tx_id = tx_payload.get("originalTransactionId")
        if product_id and original_tx_id:
            plan = await lookup_subscription_plan(db, platform="apple", product_id=product_id)
            if plan:
                receipt_stmt = select(IapReceipt).where(
                    and_(
                        IapReceipt.platform == "apple",
                        IapReceipt.original_transaction_id == original_tx_id,
                    )
                ).order_by(IapReceipt.created_at.desc()).limit(1)
                receipt_result = await db.execute(receipt_stmt)
                receipt = receipt_result.scalar_one_or_none()
                if receipt:
                    try:
                        await deactivate_subscription(
                            db, user_id=receipt.user_id, plan=plan, new_status="expired",
                        )
                    except Exception as exc:
                        logger.error("Failed to expire subscription after grace period: %s", exc)

    # Handle renewal failure and renewal status changes
    if notification_type in ("DID_FAIL_TO_RENEW", "DID_CHANGE_RENEWAL_STATUS") and transaction_id:
        product_id = tx_payload.get("productId")
        original_tx_id = tx_payload.get("originalTransactionId")
        if product_id and original_tx_id:
            plan = await lookup_subscription_plan(db, platform="apple", product_id=product_id)
            if plan:
                receipt_stmt = select(IapReceipt).where(
                    and_(
                        IapReceipt.platform == "apple",
                        IapReceipt.original_transaction_id == original_tx_id,
                    )
                ).order_by(IapReceipt.created_at.desc()).limit(1)
                receipt_result = await db.execute(receipt_stmt)
                receipt = receipt_result.scalar_one_or_none()
                if receipt:
                    if notification_type == "DID_FAIL_TO_RENEW":
                        cancel_at_end = True
                    else:
                        # DID_CHANGE_RENEWAL_STATUS
                        cancel_at_end = subtype == "AUTO_RENEW_DISABLED"
                    try:
                        await update_subscription_renewal_status(
                            db, user_id=receipt.user_id, plan=plan,
                            cancel_at_period_end=cancel_at_end,
                        )
                    except Exception as exc:
                        logger.error("Failed to update renewal status from Apple webhook: %s", exc)

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
                receipt.updated_at = datetime.now(timezone.utc)

    event.status = "processed"
    event.processed_at = datetime.now(timezone.utc)
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
    sub_notification = decoded.get("subscriptionNotification", {})
    notification_type = one_time.get("notificationType") or sub_notification.get("notificationType")
    try:
        notification_type = int(notification_type) if notification_type is not None else None
    except (TypeError, ValueError):
        notification_type = None
    purchase_token = one_time.get("purchaseToken") or sub_notification.get("purchaseToken")
    product_id = one_time.get("sku") or sub_notification.get("subscriptionId")

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
        received_at=datetime.now(timezone.utc),
    )

    try:
        db.add(event)
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return {"status": "already_processed", "event_id": event_id}

    # Handle subscription notifications — re-query Google for authoritative state
    google_sub_signal_types = {2, 4, 5, 7, 12, 13}  # renewal, recovery, expiry, revoke signals
    if sub_notification and notification_type in google_sub_signal_types and product_id and purchase_token:
        if not config.GOOGLE_IAP_PACKAGE_NAME:
            logger.error("GOOGLE_IAP_PACKAGE_NAME not configured — cannot query subscription state")
        else:
            try:
                sub_state = await get_google_subscription_state(
                    package_name=config.GOOGLE_IAP_PACKAGE_NAME,
                    purchase_token=purchase_token,
                )
                subscription_state = sub_state.get("subscriptionState", "")

                # Active or grace period — grant/renew entitlement
                if subscription_state in (
                    "SUBSCRIPTION_STATE_ACTIVE",
                    "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
                ):
                    plan = await lookup_subscription_plan(db, platform="google", product_id=product_id)
                    if plan:
                        receipt_stmt = select(IapReceipt).where(
                            and_(
                                IapReceipt.platform == "google",
                                IapReceipt.purchase_token == purchase_token,
                            )
                        )
                        receipt_result = await db.execute(receipt_stmt)
                        receipt = receipt_result.scalar_one_or_none()
                        if receipt:
                            await activate_subscription_from_iap(
                                db, user_id=receipt.user_id, plan=plan,
                                receipt_id=receipt.id, livemode=True,
                            )

                # Expired or revoked — remove entitlement
                elif subscription_state in (
                    "SUBSCRIPTION_STATE_EXPIRED",
                    "SUBSCRIPTION_STATE_REVOKED",
                ):
                    plan = await lookup_subscription_plan(db, platform="google", product_id=product_id)
                    if plan:
                        receipt_stmt = select(IapReceipt).where(
                            and_(
                                IapReceipt.platform == "google",
                                IapReceipt.purchase_token == purchase_token,
                            )
                        )
                        receipt_result = await db.execute(receipt_stmt)
                        receipt = receipt_result.scalar_one_or_none()
                        if receipt:
                            new_status = "expired" if subscription_state == "SUBSCRIPTION_STATE_EXPIRED" else "revoked"
                            await deactivate_subscription(
                                db, user_id=receipt.user_id, plan=plan, new_status=new_status,
                            )

                # On hold or paused — suspend entitlement
                elif subscription_state in (
                    "SUBSCRIPTION_STATE_ON_HOLD",
                    "SUBSCRIPTION_STATE_PAUSED",
                ):
                    plan = await lookup_subscription_plan(db, platform="google", product_id=product_id)
                    if plan:
                        receipt_stmt = select(IapReceipt).where(
                            and_(
                                IapReceipt.platform == "google",
                                IapReceipt.purchase_token == purchase_token,
                            )
                        )
                        receipt_result = await db.execute(receipt_stmt)
                        receipt = receipt_result.scalar_one_or_none()
                        if receipt:
                            status_map = {
                                "SUBSCRIPTION_STATE_ON_HOLD": "on_hold",
                                "SUBSCRIPTION_STATE_PAUSED": "paused",
                            }
                            await deactivate_subscription(
                                db, user_id=receipt.user_id, plan=plan,
                                new_status=status_map[subscription_state],
                            )

                else:
                    logger.info("Unhandled Google subscription state: %s", subscription_state)

            except Exception as exc:
                logger.error("Failed to handle Google subscription notification: %s", exc)

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
                receipt.updated_at = datetime.now(timezone.utc)

    event.status = "processed"
    event.processed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "processed", "event_id": event_id, "product_id": product_id}


async def get_transaction_history(db, *, user_id: int, page: int, page_size: int, kind: Optional[str]):
    transactions, total = await payments_repository.list_wallet_transactions_paginated(
        db, user_id=user_id, page=page, page_size=page_size, kind=kind
    )
    return {
        "transactions": [
            WalletTransactionResponse(
                id=t.id,
                amount_minor=t.amount_minor,
                amount_usd=t.amount_minor / 100.0,
                currency=t.currency,
                kind=t.kind,
                created_at=t.created_at.isoformat() if t.created_at else None,
            )
            for t in transactions
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def request_withdrawal(db, *, user, amount_usd: float, method: str, details: Optional[str]):
    amount_minor = int(amount_usd * 100)
    currency = user.wallet_currency or "usd"
    balance = await wallet_service_get_wallet_balance(db, user.account_id, currency)

    if amount_minor > balance:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient balance. Current balance: ${balance / 100:.2f}",
        )

    if amount_minor < 500:  # minimum $5 withdrawal
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Minimum withdrawal amount is $5.00",
        )

    # Debit wallet
    await adjust_wallet_balance(
        db=db,
        user_id=user.account_id,
        currency=currency,
        delta_minor=-amount_minor,
        kind="withdraw",
        external_ref_type="withdrawal_request",
        external_ref_id=f"wd_{user.account_id}_{int(datetime.now(timezone.utc).timestamp())}",
        livemode=True,
    )

    # Create withdrawal record
    withdrawal = await payments_repository.create_withdrawal(
        db, account_id=user.account_id, amount=amount_usd, method=method
    )
    await db.commit()

    return {
        "id": withdrawal.id,
        "amount": withdrawal.amount,
        "withdrawal_method": withdrawal.withdrawal_method,
        "withdrawal_status": withdrawal.withdrawal_status,
        "requested_at": withdrawal.requested_at.isoformat() if withdrawal.requested_at else None,
        "processed_at": None,
    }


async def get_withdrawal_history(db, *, account_id: int, page: int, page_size: int):
    withdrawals, total = await payments_repository.list_withdrawals_paginated(
        db, account_id=account_id, page=page, page_size=page_size
    )
    return {
        "withdrawals": [
            {
                "id": w.id,
                "amount": w.amount,
                "withdrawal_method": w.withdrawal_method,
                "withdrawal_status": w.withdrawal_status,
                "requested_at": w.requested_at.isoformat() if w.requested_at else None,
                "processed_at": w.processed_at.isoformat() if w.processed_at else None,
            }
            for w in withdrawals
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


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
