"""Payments/Wallet/IAP repository layer."""

from sqlalchemy import desc, select


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


async def get_user_by_account_id(db, *, user_id: int):
    from app.models.user import User

    stmt = select(User).where(User.account_id == user_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_wallet_currency(db, *, user_id: int) -> str:
    user = await get_user_by_account_id(db, user_id=user_id)
    return (user.wallet_currency if user and user.wallet_currency else "usd").lower()
