"""Admin Withdrawals Router - Admin approval and management of withdrawals."""

from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_db
from app.dependencies import get_admin_user
from app.models.user import User

from .schemas import WithdrawalResponse
from .service import (
    approve_withdrawal_admin as service_approve_withdrawal_admin,
    list_withdrawals_admin as service_list_withdrawals_admin,
    reject_withdrawal_admin as service_reject_withdrawal_admin,
)

router = APIRouter(prefix="/admin/withdrawals", tags=["Admin Withdrawals"])


@router.get("", response_model=List[WithdrawalResponse])
async def list_withdrawals(
    status_filter: Optional[str] = None,
    withdrawal_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    admin_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await service_list_withdrawals_admin(
        db,
        status_filter=status_filter,
        withdrawal_type=withdrawal_type,
        limit=limit,
        offset=offset,
    )


@router.post("/{withdrawal_id}/approve")
async def approve_withdrawal(
    withdrawal_id: int,
    admin_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await service_approve_withdrawal_admin(
        db, admin_user=admin_user, withdrawal_id=withdrawal_id
    )


@router.post("/{withdrawal_id}/reject")
async def reject_withdrawal(
    withdrawal_id: int,
    reason: Optional[str] = None,
    admin_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await service_reject_withdrawal_admin(
        db, admin_user=admin_user, withdrawal_id=withdrawal_id, reason=reason
    )
