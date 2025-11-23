"""
Wallet Router - User wallet operations
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, date
from app.db import get_async_db
from app.models.user import User
from app.models.wallet import WalletTransaction, WithdrawalRequest
from app.dependencies import get_current_user
from app.services.wallet_service import (
    adjust_wallet_balance,
    get_wallet_balance,
    get_daily_instant_withdrawal_count,
    calculate_withdrawal_fee
)
from app.services.stripe_service import (
    create_or_get_connect_account,
    create_payout,
    PayoutFailed
)

router = APIRouter(prefix="/wallet", tags=["Wallet"])


class WithdrawalRequestModel(BaseModel):
    amount_minor: int = Field(..., gt=0, description="Amount in minor units (cents)")
    type: str = Field(..., pattern="^(standard|instant)$", description="Withdrawal type: standard or instant")


class WalletBalanceResponse(BaseModel):
    balance_minor: int
    balance_usd: float
    currency: str
    stripe_onboarded: bool
    recent_transactions: Optional[List[dict]] = None


@router.get("/me", response_model=WalletBalanceResponse)
async def get_wallet_info(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    include_transactions: bool = False
):
    """
    Get wallet balance and information for the current user.
    """
    balance_minor = await get_wallet_balance(db, user.account_id, user.wallet_currency or 'usd')
    balance_usd = balance_minor / 100.0
    stripe_onboarded = bool(user.stripe_connect_account_id)
    
    recent_transactions = None
    if include_transactions:
        stmt = (
            select(WalletTransaction)
            .where(WalletTransaction.user_id == user.account_id)
            .order_by(desc(WalletTransaction.created_at))
            .limit(10)
        )
        result = await db.execute(stmt)
        transactions = result.scalars().all()
        recent_transactions = [
            {
                "id": t.id,
                "amount_minor": t.amount_minor,
                "amount_usd": t.amount_minor / 100.0,
                "currency": t.currency,
                "kind": t.kind,
                "created_at": t.created_at.isoformat() if t.created_at else None
            }
            for t in transactions
        ]
    
    return WalletBalanceResponse(
        balance_minor=balance_minor,
        balance_usd=balance_usd,
        currency=user.wallet_currency or 'usd',
        stripe_onboarded=stripe_onboarded,
        recent_transactions=recent_transactions
    )


@router.post("/withdraw")
async def withdraw_from_wallet(
    request: WithdrawalRequestModel,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Request a withdrawal from the user's wallet.
    
    For instant withdrawals:
    - Requires stripe_connect_account_id
    - Checks daily limit
    - Attempts immediate payout
    
    For standard withdrawals:
    - Requires stripe_connect_account_id
    - Creates pending_review request
    """
    if not user.stripe_connect_account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stripe Connect account not set up. Please complete onboarding first."
        )
    
    currency = user.wallet_currency or 'usd'
    current_balance = await get_wallet_balance(db, user.account_id, currency)
    
    # Calculate fee
    fee_minor = calculate_withdrawal_fee(request.amount_minor, request.type)
    total_debit = request.amount_minor + fee_minor
    
    # Check balance
    if current_balance < total_debit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient balance. Available: {current_balance / 100.0:.2f} {currency.upper()}, Required: {total_debit / 100.0:.2f} {currency.upper()}"
        )
    
    # For instant withdrawals, check limits
    if request.type == 'instant':
        if not user.instant_withdrawal_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Instant withdrawals are disabled for your account"
            )
        
        # Check daily limit
        today = date.today()
        daily_total = await get_daily_instant_withdrawal_count(db, user.account_id, today)
        
        if daily_total + request.amount_minor > user.instant_withdrawal_daily_limit_minor:
            remaining = user.instant_withdrawal_daily_limit_minor - daily_total
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Daily instant withdrawal limit exceeded. Remaining: {remaining / 100.0:.2f} {currency.upper()}"
            )
    
    # Lock user and adjust balance
    try:
        # Adjust wallet balance (this locks the user row)
        new_balance = await adjust_wallet_balance(
            db=db,
            user_id=user.account_id,
            currency=currency,
            delta_minor=-total_debit,
            kind='withdraw',
            external_ref_type='withdrawal_request',
            livemode=False
        )
        
        # Create withdrawal request
        withdrawal = WithdrawalRequest(
            user_id=user.account_id,
            amount_minor=request.amount_minor,
            currency=currency,
            type=request.type,
            status='pending_review' if request.type == 'standard' else 'processing',
            fee_minor=fee_minor,
            requested_at=datetime.utcnow(),
            livemode=False
        )
        db.add(withdrawal)
        await db.flush()
        
        # For instant withdrawals, attempt payout immediately
        if request.type == 'instant':
            try:
                payout_result = await create_payout(
                    connected_account_id=user.stripe_connect_account_id,
                    amount_minor=request.amount_minor,
                    currency=currency,
                    description=f"Instant withdrawal for user {user.account_id}"
                )
                
                withdrawal.stripe_payout_id = payout_result['payout_id']
                withdrawal.status = 'paid'
                withdrawal.processed_at = datetime.utcnow()
                
            except PayoutFailed as e:
                # Refund the wallet
                await adjust_wallet_balance(
                    db=db,
                    user_id=user.account_id,
                    currency=currency,
                    delta_minor=total_debit,
                    kind='refund',
                    external_ref_type='withdrawal_failed',
                    external_ref_id=str(withdrawal.id),
                    livemode=False
                )
                
                withdrawal.status = 'failed'
                withdrawal.admin_notes = f"Payout failed: {str(e)}"
                
                await db.commit()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Withdrawal failed: {str(e)}. Funds have been refunded to your wallet."
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
            "type": request.type
        }
        
    except ValueError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

