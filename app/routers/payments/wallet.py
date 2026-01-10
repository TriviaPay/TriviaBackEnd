"""Wallet Router - User wallet operations."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_db
from app.dependencies import get_current_user
from app.models.user import User

from .schemas import WalletBalanceResponse, WithdrawalRequestModel
from .service import (
    get_wallet_info as service_get_wallet_info,
    withdraw_from_wallet as service_withdraw_from_wallet,
)

router = APIRouter(prefix="/wallet", tags=["Wallet"])


@router.get("/me", response_model=WalletBalanceResponse)
async def get_wallet_info(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    include_transactions: bool = False,
):
    """
    Get wallet balance and information for the current user.
    """
    return await service_get_wallet_info(
        db, user=user, include_transactions=include_transactions
    )


@router.post("/withdraw")
async def withdraw_from_wallet(
    request: WithdrawalRequestModel,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
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
    return await service_withdraw_from_wallet(db, user=user, request=request)
