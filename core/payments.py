"""Payments/Wallet facade for other domains."""

from sqlalchemy.ext.asyncio import AsyncSession


async def credit_wallet(
    db: AsyncSession, *, account_id: int, amount_minor: int, reason: str
):
    from app.routers.payments import service as payments_service

    return await payments_service.credit_wallet(
        db, account_id=account_id, amount_minor=amount_minor, reason=reason
    )


async def debit_wallet(
    db: AsyncSession, *, account_id: int, amount_minor: int, reason: str
):
    from app.routers.payments import service as payments_service

    return await payments_service.debit_wallet(
        db, account_id=account_id, amount_minor=amount_minor, reason=reason
    )


async def get_wallet_balance(db: AsyncSession, *, account_id: int):
    from app.routers.payments import service as payments_service

    return await payments_service.get_wallet_balance(db, account_id=account_id)

