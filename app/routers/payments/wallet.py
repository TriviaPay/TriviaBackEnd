"""Wallet Router - User wallet operations."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_db
from app.dependencies import get_current_user
from app.models.user import User

from .schemas import (
    PaginatedTransactionsResponse,
    PaginatedWithdrawalsResponse,
    WalletBalanceResponse,
    WithdrawalRequest,
    WithdrawalResponse,
)
from .service import (
    get_transaction_history as service_get_transaction_history,
    get_wallet_info as service_get_wallet_info,
    get_withdrawal_history as service_get_withdrawal_history,
    request_withdrawal as service_request_withdrawal,
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


@router.get("/transactions", response_model=PaginatedTransactionsResponse)
async def get_transactions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    kind: Optional[str] = Query(None, description="Filter by transaction kind (e.g., deposit, withdraw, adjustment, iap_credit, iap_refund)"),
):
    """
    Get paginated transaction history for the current user.
    """
    return await service_get_transaction_history(
        db, user_id=user.account_id, page=page, page_size=page_size, kind=kind
    )


@router.post("/withdraw", response_model=WithdrawalResponse)
async def request_withdraw(
    request: WithdrawalRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Request a withdrawal from the wallet. Minimum $5.00.
    Debits the wallet immediately; processing happens offline.
    """
    return await service_request_withdrawal(
        db, user=user, amount_usd=request.amount_usd, method=request.method, details=request.details
    )


@router.get("/withdrawals", response_model=PaginatedWithdrawalsResponse)
async def get_withdrawals(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
):
    """
    Get paginated withdrawal history for the current user.
    """
    return await service_get_withdrawal_history(
        db, account_id=user.account_id, page=page, page_size=page_size
    )
