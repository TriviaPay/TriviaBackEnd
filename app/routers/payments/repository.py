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


async def list_withdrawals_with_users(
    db,
    *,
    status_filter: str,
    withdrawal_type,
    limit: int,
    offset: int,
):
    from app.models.user import User
    from app.models.wallet import WithdrawalRequest

    stmt = (
        select(WithdrawalRequest, User)
        .join(User, WithdrawalRequest.user_id == User.account_id)
        .where(WithdrawalRequest.status == status_filter)
        .order_by(desc(WithdrawalRequest.requested_at))
        .limit(limit)
        .offset(offset)
    )
    if withdrawal_type:
        stmt = stmt.where(WithdrawalRequest.type == withdrawal_type)
    result = await db.execute(stmt)
    return result.all()


async def lock_withdrawal(db, *, withdrawal_id: int):
    from app.models.wallet import WithdrawalRequest

    stmt = (
        select(WithdrawalRequest)
        .where(WithdrawalRequest.id == withdrawal_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def lock_user(db, *, user_id: int):
    from app.models.user import User

    stmt = select(User).where(User.account_id == user_id).with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_wallet_transaction_by_event_id(db, *, event_id: str):
    from app.models.wallet import WalletTransaction

    stmt = select(WalletTransaction).where(WalletTransaction.event_id == event_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_wallet_transaction_by_external_ref(
    db, *, external_ref_type: str, external_ref_id: str
):
    from app.models.wallet import WalletTransaction

    stmt = select(WalletTransaction).where(
        WalletTransaction.external_ref_type == external_ref_type,
        WalletTransaction.external_ref_id == external_ref_id,
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def record_webhook_event(db, *, event_id: str, livemode: bool):
    from datetime import datetime

    from app.models.wallet import WalletTransaction

    transaction = WalletTransaction(
        user_id=0,
        amount_minor=0,
        currency="usd",
        kind="webhook_event",
        external_ref_type="stripe_webhook",
        external_ref_id=event_id,
        event_id=event_id,
        livemode=livemode,
        created_at=datetime.utcnow(),
    )
    db.add(transaction)
    return transaction


async def get_user_by_connect_account_id(db, *, account_id: str):
    from app.models.user import User

    stmt = select(User).where(User.stripe_connect_account_id == account_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_withdrawal_by_payout_id(db, *, payout_id: str):
    from app.models.wallet import WithdrawalRequest

    stmt = select(WithdrawalRequest).where(WithdrawalRequest.stripe_payout_id == payout_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_by_account_id(db, *, user_id: int):
    from app.models.user import User

    stmt = select(User).where(User.account_id == user_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_wallet_currency(db, *, user_id: int) -> str:
    user = await get_user_by_account_id(db, user_id=user_id)
    return (user.wallet_currency if user and user.wallet_currency else "usd").lower()
