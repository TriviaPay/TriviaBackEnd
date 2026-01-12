"""Payments/Wallet/IAP service layer."""

import os
from datetime import date, datetime
from typing import Optional

from fastapi import HTTPException, status

import core.config as config
from app.services.apple_iap_service import process_apple_iap
from app.services.google_iap_service import process_google_iap
from app.services.iap_service import get_product_credit_amount
from app.services.stripe_service import PayoutFailed, create_payout
from app.services.stripe_service import (
    StripeError,
    create_account_link,
    create_ephemeral_key_for_customer,
    create_or_get_connect_account,
    create_payment_intent_for_topup,
    get_or_create_stripe_customer_for_user,
    get_publishable_key,
    verify_webhook_signature,
)
from app.services.wallet_service import (
    adjust_wallet_balance,
    calculate_withdrawal_fee,
    get_daily_instant_withdrawal_count,
    get_wallet_balance as wallet_service_get_wallet_balance,
)

from . import repository as payments_repository
from .schemas import (
    AccountLinkResponse,
    AppleVerifyRequest,
    GoogleVerifyRequest,
    IapVerifyResponse,
    PaymentConfigResponse,
    PaymentSheetInitRequest,
    PaymentSheetResponse,
    WalletBalanceResponse,
    WalletTransactionResponse,
    WithdrawalResponse,
)


async def get_wallet_info(db, *, user, include_transactions: bool):
    currency = user.wallet_currency or "usd"
    balance_minor = await wallet_service_get_wallet_balance(db, user.account_id, currency)
    stripe_onboarded = bool(user.stripe_connect_account_id)

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
        stripe_onboarded=stripe_onboarded,
        recent_transactions=recent_transactions,
    )


async def withdraw_from_wallet(db, *, user, request):
    if not user.stripe_connect_account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stripe Connect account not set up. Please complete onboarding first.",
        )

    currency = user.wallet_currency or "usd"
    current_balance = await wallet_service_get_wallet_balance(db, user.account_id, currency)

    fee_minor = calculate_withdrawal_fee(request.amount_minor, request.type)
    total_debit = request.amount_minor + fee_minor

    if current_balance < total_debit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Insufficient balance. Available: {current_balance / 100.0:.2f} {currency.upper()}, "
                f"Required: {total_debit / 100.0:.2f} {currency.upper()}"
            ),
        )

    if request.type == "instant":
        if not user.instant_withdrawal_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Instant withdrawals are disabled for your account",
            )

        today = date.today()
        daily_total = await get_daily_instant_withdrawal_count(
            db, user.account_id, today
        )
        if daily_total + request.amount_minor > user.instant_withdrawal_daily_limit_minor:
            remaining = user.instant_withdrawal_daily_limit_minor - daily_total
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Daily instant withdrawal limit exceeded. Remaining: "
                    f"{remaining / 100.0:.2f} {currency.upper()}"
                ),
            )

    try:
        new_balance = await adjust_wallet_balance(
            db=db,
            user_id=user.account_id,
            currency=currency,
            delta_minor=-total_debit,
            kind="withdraw",
            external_ref_type="withdrawal_request",
            livemode=False,
        )

        withdrawal = payments_repository.create_withdrawal_request(
            user_id=user.account_id,
            amount_minor=request.amount_minor,
            currency=currency,
            withdrawal_type=request.type,
            status="pending_review" if request.type == "standard" else "processing",
            fee_minor=fee_minor,
            requested_at=datetime.utcnow(),
            livemode=False,
        )
        db.add(withdrawal)
        await db.flush()

        if request.type == "instant":
            try:
                payout_result = await create_payout(
                    connected_account_id=user.stripe_connect_account_id,
                    amount_minor=request.amount_minor,
                    currency=currency,
                    description=f"Instant withdrawal for user {user.account_id}",
                )
                withdrawal.stripe_payout_id = payout_result["payout_id"]
                withdrawal.status = "paid"
                withdrawal.processed_at = datetime.utcnow()

            except PayoutFailed as exc:
                await adjust_wallet_balance(
                    db=db,
                    user_id=user.account_id,
                    currency=currency,
                    delta_minor=total_debit,
                    kind="refund",
                    external_ref_type="withdrawal_failed",
                    external_ref_id=str(withdrawal.id),
                    livemode=False,
                )

                withdrawal.status = "failed"
                withdrawal.admin_notes = f"Payout failed: {str(exc)}"

                await db.commit()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        f"Withdrawal failed: {str(exc)}. Funds have been refunded to your wallet."
                    ),
                )

        await db.commit()

        return {
            "success": True,
            "withdrawal_id": withdrawal.id,
            "amount_minor": request.amount_minor,
            "amount_usd": request.amount_minor / 100.0,
            "fee_minor": fee_minor,
            "fee_usd": fee_minor / 100.0,
            "total_debit_minor": total_debit,
            "total_debit_usd": total_debit / 100.0,
            "status": withdrawal.status,
            "new_balance_minor": new_balance,
            "new_balance_usd": new_balance / 100.0,
            "type": request.type,
        }

    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def get_payment_config() -> PaymentConfigResponse:
    publishable_key = get_publishable_key()
    if not publishable_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe publishable key not configured",
        )

    currency = os.getenv("PAYMENTS_DEFAULT_CURRENCY", "usd")
    return PaymentConfigResponse(publishable_key=publishable_key, currency=currency)


async def initialize_payment_sheet(db, *, user, request: PaymentSheetInitRequest) -> PaymentSheetResponse:
    if request.topup_type == "wallet_topup":
        if not request.amount_minor or request.amount_minor <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="amount_minor is required and must be greater than 0 for wallet_topup",
            )
        amount_minor = request.amount_minor
        product_id = None
    elif request.topup_type == "product":
        if not request.product_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="product_id is required for product purchases",
            )
        amount_minor = await get_product_credit_amount(db, request.product_id)
        if amount_minor is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Product {request.product_id} not found or has no price",
            )
        product_id = request.product_id
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid topup_type: {request.topup_type}",
        )

    currency = request.currency or os.getenv("PAYMENTS_DEFAULT_CURRENCY", "usd")

    try:
        customer_id = await get_or_create_stripe_customer_for_user(user, db)
        ephemeral_key = create_ephemeral_key_for_customer(
            customer_id, stripe_api_version="2023-10-16"
        )
        payment_intent = create_payment_intent_for_topup(
            amount_minor=amount_minor,
            currency=currency,
            user=user,
            topup_type=request.topup_type,
            product_id=product_id,
        )
        return PaymentSheetResponse(
            customerId=customer_id,
            ephemeralKeySecret=ephemeral_key.secret,
            paymentIntentClientSecret=payment_intent.client_secret,
            amount_minor=amount_minor,
            currency=currency,
            topup_type=request.topup_type,
            product_id=product_id,
        )
    except StripeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialize payment sheet: {str(exc)}",
        )


async def create_connect_account_link(
    db, *, user, return_url: Optional[str], refresh_url: Optional[str]
) -> AccountLinkResponse:
    try:
        account_id = await create_or_get_connect_account(user)
        if not user.stripe_connect_account_id:
            user.stripe_connect_account_id = account_id
            await db.commit()
            await db.refresh(user)

        link_result = await create_account_link(account_id, return_url, refresh_url)
        return AccountLinkResponse(url=link_result["url"], account_id=account_id)
    except StripeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        )


async def refresh_connect_account_link(
    *, user, return_url: Optional[str], refresh_url: Optional[str]
) -> AccountLinkResponse:
    if not user.stripe_connect_account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Stripe Connect account found. Please create one first.",
        )

    try:
        link_result = await create_account_link(
            user.stripe_connect_account_id, return_url, refresh_url
        )
        return AccountLinkResponse(
            url=link_result["url"], account_id=user.stripe_connect_account_id
        )
    except StripeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        )


def get_publishable_key_public():
    publishable_key = get_publishable_key()
    if not publishable_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe publishable key not configured",
        )
    return {"publishable_key": publishable_key}


async def verify_apple_purchase(db, *, user, request: AppleVerifyRequest) -> IapVerifyResponse:
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


async def list_withdrawals_admin(
    db,
    *,
    status_filter: Optional[str],
    withdrawal_type: Optional[str],
    limit: int,
    offset: int,
):
    if status_filter is None:
        status_filter = "pending_review"

    rows = await payments_repository.list_withdrawals_with_users(
        db,
        status_filter=status_filter,
        withdrawal_type=withdrawal_type,
        limit=limit,
        offset=offset,
    )
    withdrawals = []
    for withdrawal, user in rows:
        withdrawals.append(
            WithdrawalResponse(
                id=withdrawal.id,
                user_id=withdrawal.user_id,
                username=user.username,
                email=user.email,
                amount_minor=withdrawal.amount_minor,
                amount_usd=withdrawal.amount_minor / 100.0,
                currency=withdrawal.currency,
                type=withdrawal.type,
                status=withdrawal.status,
                fee_minor=withdrawal.fee_minor,
                fee_usd=withdrawal.fee_minor / 100.0,
                stripe_payout_id=withdrawal.stripe_payout_id,
                requested_at=withdrawal.requested_at,
                processed_at=withdrawal.processed_at,
                admin_notes=withdrawal.admin_notes,
            )
        )
    return withdrawals


async def approve_withdrawal_admin(db, *, admin_user, withdrawal_id: int):
    withdrawal = await payments_repository.lock_withdrawal(db, withdrawal_id=withdrawal_id)
    if not withdrawal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Withdrawal request not found"
        )
    if withdrawal.status != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Withdrawal is not pending review. Current status: {withdrawal.status}",
        )

    user = await payments_repository.lock_user(db, user_id=withdrawal.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not user.stripe_connect_account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have a Stripe Connect account",
        )

    try:
        payout_result = await create_payout(
            connected_account_id=user.stripe_connect_account_id,
            amount_minor=withdrawal.amount_minor,
            currency=withdrawal.currency,
            description=f"Approved withdrawal {withdrawal_id} for user {user.account_id}",
        )
        withdrawal.stripe_payout_id = payout_result["payout_id"]
        withdrawal.status = "paid"
        withdrawal.processed_at = datetime.utcnow()
        withdrawal.admin_id = admin_user.account_id

        await db.commit()
        return {
            "success": True,
            "withdrawal_id": withdrawal.id,
            "payout_id": payout_result["payout_id"],
            "status": withdrawal.status,
        }
    except PayoutFailed as exc:
        withdrawal.status = "failed"
        withdrawal.processed_at = datetime.utcnow()
        withdrawal.admin_id = admin_user.account_id
        withdrawal.admin_notes = f"Payout failed: {str(exc)}"

        await adjust_wallet_balance(
            db=db,
            user_id=withdrawal.user_id,
            currency=withdrawal.currency,
            delta_minor=withdrawal.amount_minor + withdrawal.fee_minor,
            kind="refund",
            external_ref_type="withdrawal_failed",
            external_ref_id=str(withdrawal.id),
            livemode=False,
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Payout failed: {str(exc)}. Funds have been refunded to user's wallet.",
        )


async def reject_withdrawal_admin(
    db, *, admin_user, withdrawal_id: int, reason: Optional[str]
):
    withdrawal = await payments_repository.lock_withdrawal(db, withdrawal_id=withdrawal_id)
    if not withdrawal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Withdrawal request not found"
        )
    if withdrawal.status != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Withdrawal is not pending review. Current status: {withdrawal.status}",
        )

    await adjust_wallet_balance(
        db=db,
        user_id=withdrawal.user_id,
        currency=withdrawal.currency,
        delta_minor=withdrawal.amount_minor + withdrawal.fee_minor,
        kind="refund",
        external_ref_type="withdrawal_rejected",
        external_ref_id=str(withdrawal.id),
        livemode=False,
    )

    withdrawal.status = "rejected"
    withdrawal.processed_at = datetime.utcnow()
    withdrawal.admin_id = admin_user.account_id
    withdrawal.admin_notes = reason or "Withdrawal rejected by admin"

    await db.commit()
    return {
        "success": True,
        "withdrawal_id": withdrawal.id,
        "status": withdrawal.status,
        "message": "Withdrawal rejected and funds refunded",
    }


async def process_stripe_webhook(db, *, request, stripe_signature: Optional[str]):
    from app.models.user import User
    from app.models.wallet import WalletTransaction, WithdrawalRequest

    if not stripe_signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header",
        )

    body = await request.body()
    try:
        event = verify_webhook_signature(body, stripe_signature)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature"
        )

    event_type = event["type"]
    event_id = event["id"]
    livemode = event["livemode"]

    existing = await payments_repository.get_wallet_transaction_by_event_id(db, event_id=event_id)
    if existing:
        return {"status": "already_processed", "event_id": event_id}

    try:
        if event_type == "account.updated":
            account = event["data"]["object"]
            account_id = account.get("id")
            charges_enabled = account.get("charges_enabled", False)
            payouts_enabled = account.get("payouts_enabled", False)
            details_submitted = account.get("details_submitted", False)

            user = await payments_repository.get_user_by_connect_account_id(db, account_id=account_id)
            if user:
                if hasattr(user, "stripe_charges_enabled"):
                    user.stripe_charges_enabled = charges_enabled
                if hasattr(user, "stripe_payouts_enabled"):
                    user.stripe_payouts_enabled = payouts_enabled
                if hasattr(user, "stripe_details_submitted"):
                    user.stripe_details_submitted = details_submitted
                await db.commit()

        elif event_type in ["transfer.paid", "payout.paid"]:
            transfer = event["data"]["object"]
            transfer_id = transfer.get("id")
            withdrawal = await payments_repository.get_withdrawal_by_payout_id(db, payout_id=transfer_id)
            if withdrawal and withdrawal.status != "paid":
                withdrawal.status = "paid"
                withdrawal.processed_at = datetime.utcnow()
                await db.commit()

        elif event_type in ["transfer.failed", "payout.failed"]:
            transfer = event["data"]["object"]
            transfer_id = transfer.get("id")
            withdrawal = await payments_repository.get_withdrawal_by_payout_id(db, payout_id=transfer_id)
            if withdrawal and withdrawal.status != "failed":
                refund_amount = withdrawal.amount_minor + withdrawal.fee_minor
                await adjust_wallet_balance(
                    db=db,
                    user_id=withdrawal.user_id,
                    currency=withdrawal.currency,
                    delta_minor=refund_amount,
                    kind="refund",
                    external_ref_type="withdrawal_failed",
                    external_ref_id=str(withdrawal.id),
                    livemode=livemode,
                )
                withdrawal.status = "failed"
                withdrawal.processed_at = datetime.utcnow()
                withdrawal.admin_notes = f"Payout failed via webhook: {transfer_id}"
                await db.commit()

        elif event_type == "payment_intent.succeeded":
            pi = event["data"]["object"]
            payment_intent_id = pi["id"]
            amount_minor = pi["amount"]
            currency = pi["currency"]
            metadata = pi.get("metadata", {})
            account_id_str = metadata.get("account_id")
            topup_type = metadata.get("topup_type")

            if not account_id_str:
                await payments_repository.record_webhook_event(db, event_id=event_id, livemode=livemode)
                await db.commit()
                return {"received": True, "status": "skipped_missing_account_id"}

            try:
                account_id = int(account_id_str)
            except (ValueError, TypeError):
                await payments_repository.record_webhook_event(db, event_id=event_id, livemode=livemode)
                await db.commit()
                return {"received": True, "status": "skipped_invalid_account_id"}

            user = await payments_repository.get_user_by_account_id(db, user_id=account_id)
            if not user:
                await payments_repository.record_webhook_event(db, event_id=event_id, livemode=livemode)
                await db.commit()
                return {"received": True, "status": "skipped_user_not_found"}

            existing_pi = await payments_repository.get_wallet_transaction_by_external_ref(
                db, external_ref_type="stripe_payment_intent", external_ref_id=payment_intent_id
            )
            if existing_pi:
                await payments_repository.record_webhook_event(db, event_id=event_id, livemode=livemode)
                await db.commit()
                return {"status": "already_processed", "event_id": event_id, "payment_intent_id": payment_intent_id}

            kind = "deposit"
            if topup_type == "product":
                kind = "product_purchase_credit"

            new_balance = await adjust_wallet_balance(
                db=db,
                user_id=user.account_id,
                currency=currency,
                delta_minor=amount_minor,
                kind=kind,
                external_ref_type="stripe_payment_intent",
                external_ref_id=payment_intent_id,
                event_id=payment_intent_id,
                livemode=livemode,
            )
            await db.commit()
            return {
                "received": True,
                "status": "processed",
                "event_id": event_id,
                "payment_intent_id": payment_intent_id,
                "user_id": user.account_id,
                "amount_minor": amount_minor,
                "new_balance_minor": new_balance,
            }

        await payments_repository.record_webhook_event(db, event_id=event_id, livemode=livemode)
        await db.commit()
        return {"status": "processed", "event_id": event_id, "event_type": event_type}
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing webhook",
        )


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
