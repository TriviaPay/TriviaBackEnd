"""
Admin Withdrawals Router - Admin approval and management of withdrawals
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_db
from app.dependencies import get_admin_user
from app.models.user import User
from app.models.wallet import WithdrawalRequest
from app.services.stripe_service import PayoutFailed, create_payout
from app.services.wallet_service import adjust_wallet_balance

router = APIRouter(prefix="/admin/withdrawals", tags=["Admin Withdrawals"])


class WithdrawalResponse(BaseModel):
    id: int
    user_id: int
    username: Optional[str]
    email: Optional[str]
    amount_minor: int
    amount_usd: float
    currency: str
    type: str
    status: str
    fee_minor: int
    fee_usd: float
    stripe_payout_id: Optional[str]
    requested_at: datetime
    processed_at: Optional[datetime]
    admin_notes: Optional[str]


@router.get("", response_model=List[WithdrawalResponse])
async def list_withdrawals(
    status_filter: Optional[str] = None,
    withdrawal_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    admin_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List withdrawal requests with optional filters.

    Default filter: status=pending_review
    """
    if status_filter is None:
        status_filter = "pending_review"

    stmt = (
        select(WithdrawalRequest, User)
        .join(User, WithdrawalRequest.user_id == User.account_id)
        .where(WithdrawalRequest.status == status_filter)
    )

    if withdrawal_type:
        stmt = stmt.where(WithdrawalRequest.type == withdrawal_type)

    stmt = (
        stmt.order_by(desc(WithdrawalRequest.requested_at)).limit(limit).offset(offset)
    )

    result = await db.execute(stmt)
    rows = result.all()

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


@router.post("/{withdrawal_id}/approve")
async def approve_withdrawal(
    withdrawal_id: int,
    admin_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Approve a withdrawal request and create Stripe payout.

    Locks withdrawal and user rows, creates payout, updates status.
    """
    # Lock withdrawal row
    stmt = (
        select(WithdrawalRequest)
        .where(WithdrawalRequest.id == withdrawal_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    withdrawal = result.scalar_one_or_none()

    if not withdrawal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Withdrawal request not found"
        )

    if withdrawal.status != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Withdrawal is not pending review. Current status: {withdrawal.status}",
        )

    # Lock user row
    user_stmt = (
        select(User).where(User.account_id == withdrawal.user_id).with_for_update()
    )
    user_result = await db.execute(user_stmt)
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    if not user.stripe_connect_account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have a Stripe Connect account",
        )

    try:
        # Create Stripe payout
        payout_result = await create_payout(
            connected_account_id=user.stripe_connect_account_id,
            amount_minor=withdrawal.amount_minor,
            currency=withdrawal.currency,
            description=f"Approved withdrawal {withdrawal_id} for user {user.account_id}",
        )

        # Update withdrawal
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

    except PayoutFailed as e:
        # Mark as failed
        withdrawal.status = "failed"
        withdrawal.processed_at = datetime.utcnow()
        withdrawal.admin_id = admin_user.account_id
        withdrawal.admin_notes = f"Payout failed: {str(e)}"

        # Refund wallet
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
            detail=f"Payout failed: {str(e)}. Funds have been refunded to user's wallet.",
        )


@router.post("/{withdrawal_id}/reject")
async def reject_withdrawal(
    withdrawal_id: int,
    reason: Optional[str] = None,
    admin_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Reject a withdrawal request and refund wallet.
    """
    # Lock withdrawal row
    stmt = (
        select(WithdrawalRequest)
        .where(WithdrawalRequest.id == withdrawal_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    withdrawal = result.scalar_one_or_none()

    if not withdrawal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Withdrawal request not found"
        )

    if withdrawal.status != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Withdrawal is not pending review. Current status: {withdrawal.status}",
        )

    # Refund wallet
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

    # Update withdrawal
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
