"""Payments/Wallet/IAP repository layer."""

from typing import Optional

from sqlalchemy import desc, func, literal, select, union_all


async def list_recent_wallet_transactions(db, *, user_id: int, limit: int = 10):
    from app.models.wallet import WalletTransaction

    stmt = (
        select(WalletTransaction)
        .where(WalletTransaction.user_id == user_id)
        .order_by(desc(WalletTransaction.created_at))
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


async def list_user_draw_earnings(db, *, user_id: int):
    from models import TriviaBronzeModeLeaderboard, TriviaSilverModeLeaderboard

    bronze_stmt = select(
        TriviaBronzeModeLeaderboard.draw_date.label("draw_date"),
        TriviaBronzeModeLeaderboard.money_awarded.label("amount_usd"),
        TriviaBronzeModeLeaderboard.submitted_at.label("submitted_at"),
        literal("bronze").label("subscription_type"),
        literal("Bronze Mode").label("subscription_name"),
        literal(5.0).label("subscription_amount_usd"),
    ).where(
        TriviaBronzeModeLeaderboard.account_id == user_id,
        TriviaBronzeModeLeaderboard.money_awarded > 0,
    )

    silver_stmt = select(
        TriviaSilverModeLeaderboard.draw_date.label("draw_date"),
        TriviaSilverModeLeaderboard.money_awarded.label("amount_usd"),
        TriviaSilverModeLeaderboard.submitted_at.label("submitted_at"),
        literal("silver").label("subscription_type"),
        literal("Silver Mode").label("subscription_name"),
        literal(10.0).label("subscription_amount_usd"),
    ).where(
        TriviaSilverModeLeaderboard.account_id == user_id,
        TriviaSilverModeLeaderboard.money_awarded > 0,
    )

    earnings = union_all(bronze_stmt, silver_stmt).subquery()
    stmt = select(
        earnings.c.draw_date,
        earnings.c.amount_usd,
        earnings.c.submitted_at,
        earnings.c.subscription_type,
        earnings.c.subscription_name,
        earnings.c.subscription_amount_usd,
    ).order_by(
        desc(earnings.c.draw_date),
        desc(earnings.c.submitted_at),
        desc(earnings.c.amount_usd),
    )

    result = await db.execute(stmt)
    return result.all()


async def list_wallet_transactions_paginated(
    db, *, user_id: int, page: int = 1, page_size: int = 20, kind: Optional[str] = None
):
    from app.models.wallet import WalletTransaction

    filters = [WalletTransaction.user_id == user_id]
    if kind:
        filters.append(WalletTransaction.kind == kind)

    count_stmt = select(func.count()).select_from(WalletTransaction).where(*filters)
    total = (await db.execute(count_stmt)).scalar()

    stmt = (
        select(WalletTransaction)
        .where(*filters)
        .order_by(desc(WalletTransaction.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return result.scalars().all(), total


async def list_withdrawals_paginated(
    db, *, account_id: int, page: int = 1, page_size: int = 20
):
    from models import Withdrawal

    filters = [Withdrawal.account_id == account_id]

    count_stmt = select(func.count()).select_from(Withdrawal).where(*filters)
    total = (await db.execute(count_stmt)).scalar()

    stmt = (
        select(Withdrawal)
        .where(*filters)
        .order_by(desc(Withdrawal.requested_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    return result.scalars().all(), total


async def create_withdrawal(db, *, account_id: int, amount: float, method: str):
    from datetime import datetime

    from models import Withdrawal

    withdrawal = Withdrawal(
        account_id=account_id,
        amount=amount,
        withdrawal_method=method,
        withdrawal_status="requested",
        requested_at=datetime.utcnow(),
    )
    db.add(withdrawal)
    await db.flush()
    return withdrawal


async def get_user_by_account_id(db, *, user_id: int):
    from app.models.user import User

    stmt = select(User).where(User.account_id == user_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_wallet_currency(db, *, user_id: int) -> str:
    user = await get_user_by_account_id(db, user_id=user_id)
    return (user.wallet_currency if user and user.wallet_currency else "usd").lower()
