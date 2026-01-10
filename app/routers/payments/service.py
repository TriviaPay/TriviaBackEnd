"""Payments/Wallet/IAP service layer."""

from datetime import date, datetime

from fastapi import HTTPException, status

from app.services.stripe_service import PayoutFailed, create_payout
from app.services.wallet_service import (
    adjust_wallet_balance,
    calculate_withdrawal_fee,
    get_daily_instant_withdrawal_count,
    get_wallet_balance,
)

from . import repository as payments_repository
from .schemas import WalletBalanceResponse, WalletTransactionResponse


async def get_wallet_info(db, *, user, include_transactions: bool):
    currency = user.wallet_currency or "usd"
    balance_minor = await get_wallet_balance(db, user.account_id, currency)
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
    current_balance = await get_wallet_balance(db, user.account_id, currency)

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
