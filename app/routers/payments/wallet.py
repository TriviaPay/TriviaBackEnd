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


@router.get(
    "/me",
    response_model=WalletBalanceResponse,
    summary="Get wallet balance",
    description=(
        "Returns the authenticated user's current wallet balance. "
        "Optionally includes the 10 most recent transactions when "
        "`include_transactions=true`."
    ),
    responses={
        200: {"description": "Wallet balance retrieved successfully"},
        401: {"description": "Not authenticated — missing or invalid bearer token"},
    },
)
async def get_wallet_info(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    include_transactions: bool = Query(
        False,
        description=(
            "When true, the response includes `recent_transactions` — "
            "the last 10 wallet ledger entries. Useful for a quick "
            "preview on the wallet screen without a separate API call."
        ),
    ),
):
    return await service_get_wallet_info(
        db, user=user, include_transactions=include_transactions
    )


@router.get(
    "/transactions",
    response_model=PaginatedTransactionsResponse,
    summary="Get transaction history",
    description=(
        "Returns a paginated list of all wallet transactions for the "
        "authenticated user, ordered newest-first. "
        "Use the `kind` filter to show only specific transaction types "
        "(e.g., only withdrawals or only rewards)."
    ),
    responses={
        200: {"description": "Transaction list retrieved successfully"},
        401: {"description": "Not authenticated"},
    },
)
async def get_transactions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    page: int = Query(
        1, ge=1, description="Page number (1-based)."
    ),
    page_size: int = Query(
        20,
        ge=1,
        le=100,
        description="Number of transactions per page (max 100).",
    ),
    kind: Optional[str] = Query(
        None,
        description=(
            "Filter by transaction kind. Accepted values: "
            "`deposit`, `withdraw`, `iap_credit`, `iap_refund`, "
            "`trivia_reward`, `adjustment`, `fee`. "
            "Omit to return all kinds."
        ),
        example="trivia_reward",
    ),
):
    return await service_get_transaction_history(
        db, user_id=user.account_id, page=page, page_size=page_size, kind=kind
    )


@router.post(
    "/withdraw",
    response_model=WithdrawalResponse,
    summary="Request a withdrawal",
    description=(
        "Submit a withdrawal request. The requested amount is immediately "
        "debited from the wallet. Processing (actual payout to PayPal or bank) "
        "happens offline and the status can be tracked via `GET /wallet/withdrawals`.\n\n"
        "**Constraints:**\n"
        "- Minimum withdrawal: **$5.00**\n"
        "- Wallet balance must be >= requested amount\n"
        "- One withdrawal request is created per call"
    ),
    responses={
        200: {"description": "Withdrawal request created successfully"},
        400: {
            "description": (
                "Validation error — insufficient balance, below minimum, "
                "or invalid method"
            )
        },
        401: {"description": "Not authenticated"},
    },
)
async def request_withdraw(
    request: WithdrawalRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await service_request_withdrawal(
        db, user=user, amount_usd=request.amount_usd, method=request.method, details=request.details
    )


@router.get(
    "/withdrawals",
    response_model=PaginatedWithdrawalsResponse,
    summary="Get withdrawal history",
    description=(
        "Returns a paginated list of all withdrawal requests for the "
        "authenticated user, ordered newest-first. "
        "Use this to track the status of pending and completed withdrawals."
    ),
    responses={
        200: {"description": "Withdrawal list retrieved successfully"},
        401: {"description": "Not authenticated"},
    },
)
async def get_withdrawals(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    page: int = Query(1, ge=1, description="Page number (1-based)."),
    page_size: int = Query(
        20,
        ge=1,
        le=100,
        description="Number of withdrawals per page (max 100).",
    ),
):
    return await service_get_withdrawal_history(
        db, account_id=user.account_id, page=page, page_size=page_size
    )
