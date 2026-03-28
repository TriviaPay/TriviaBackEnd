"""
Gem credit/debit service.

The wallet service (adjust_wallet_balance) operates on user.wallet_balance_minor
(USD cents). Gems are a separate field (user.gems). This module provides
dedicated helpers so the two are never confused.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

logger = logging.getLogger(__name__)


async def credit_gems(
    db: AsyncSession,
    *,
    user_id: int,
    amount: int,
    reason: str,
    ref_type: str,
    ref_id: str,
) -> int:
    """Add gems to a user. Returns new gem balance. Uses row-level lock."""
    result = await db.execute(
        select(User).where(User.account_id == user_id).with_for_update()
    )
    user = result.scalar_one()
    user.gems = (user.gems or 0) + amount
    logger.info(
        "credit_gems: user=%s amount=%d reason=%s ref=%s:%s new_balance=%d",
        user_id,
        amount,
        reason,
        ref_type,
        ref_id,
        user.gems,
    )
    return user.gems


async def debit_gems(
    db: AsyncSession,
    *,
    user_id: int,
    amount: int,
    reason: str,
    ref_type: str,
    ref_id: str,
) -> int:
    """Remove gems from a user. Clamps to zero. Returns new gem balance."""
    result = await db.execute(
        select(User).where(User.account_id == user_id).with_for_update()
    )
    user = result.scalar_one()
    current = user.gems or 0
    actual_debit = min(amount, current)
    user.gems = current - actual_debit
    if actual_debit < amount:
        logger.warning(
            "debit_gems: clamped user=%s requested=%d actual=%d reason=%s ref=%s:%s",
            user_id,
            amount,
            actual_debit,
            reason,
            ref_type,
            ref_id,
        )
    else:
        logger.info(
            "debit_gems: user=%s amount=%d reason=%s ref=%s:%s new_balance=%d",
            user_id,
            amount,
            reason,
            ref_type,
            ref_id,
            user.gems,
        )
    return user.gems
