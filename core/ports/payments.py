from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class PaymentsPort(Protocol):
    async def credit_wallet(
        self,
        db: AsyncSession,
        *,
        account_id: int,
        amount_minor: int,
        reason: str,
    ): ...

    async def debit_wallet(
        self,
        db: AsyncSession,
        *,
        account_id: int,
        amount_minor: int,
        reason: str,
    ): ...

    async def get_wallet_balance(self, db: AsyncSession, *, account_id: int) -> int: ...

