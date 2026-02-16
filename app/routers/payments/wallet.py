"""Wallet Router - User wallet operations."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_db
from app.dependencies import get_current_user
from app.models.user import User

from .schemas import WalletBalanceResponse
from .service import get_wallet_info as service_get_wallet_info

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
