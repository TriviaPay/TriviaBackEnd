"""Payments/Wallet/IAP schemas."""

from typing import List, Optional

from pydantic import BaseModel, Field


class WithdrawalRequestModel(BaseModel):
    amount_minor: int = Field(..., gt=0, description="Amount in minor units (cents)")
    type: str = Field(
        ...,
        pattern="^(standard|instant)$",
        description="Withdrawal type: standard or instant",
    )


class WalletTransactionResponse(BaseModel):
    id: int
    amount_minor: int
    amount_usd: float
    currency: str
    kind: str
    created_at: Optional[str] = None


class WalletBalanceResponse(BaseModel):
    balance_minor: int
    balance_usd: float
    currency: str
    stripe_onboarded: bool
    recent_transactions: Optional[List[WalletTransactionResponse]] = None
