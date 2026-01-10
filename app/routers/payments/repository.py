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


def create_withdrawal_request(
    *,
    user_id: int,
    amount_minor: int,
    currency: str,
    withdrawal_type: str,
    status: str,
    fee_minor: int,
    requested_at,
    livemode: bool,
):
    from app.models.wallet import WithdrawalRequest

    return WithdrawalRequest(
        user_id=user_id,
        amount_minor=amount_minor,
        currency=currency,
        type=withdrawal_type,
        status=status,
        fee_minor=fee_minor,
        requested_at=requested_at,
        livemode=livemode,
    )
