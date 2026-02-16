"""Payments/Wallet/IAP schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


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
    recent_transactions: Optional[List[WalletTransactionResponse]] = None

class AppleVerifyRequest(BaseModel):
    signed_transaction_info: str = Field(
        ..., description="StoreKit 2 signedTransactionInfo (JWS)"
    )
    product_id: str = Field(
        ..., description="Product ID to verify (e.g., GP001, AV001)"
    )
    app_account_token: Optional[str] = Field(
        default=None,
        description="Optional appAccountToken to bind purchase to a user",
    )
    environment: Optional[Literal["sandbox", "production"]] = Field(
        default="production",
        description="Environment: 'sandbox' for testing, 'production' for live purchases",
    )


class GoogleVerifyRequest(BaseModel):
    package_name: Optional[str] = Field(
        default=None,
        description="Android app package name (defaults to GOOGLE_IAP_PACKAGE_NAME from config)",
    )
    product_id: str = Field(
        ..., description="Product ID from the purchase (e.g., GP001, AV001)"
    )
    purchase_token: str = Field(
        ..., description="Purchase token from Google Play Billing"
    )


class IapVerifyResponse(BaseModel):
    success: bool
    platform: str
    transaction_id: str
    product_id: str
    credited_amount_minor: Optional[int]
    credited_amount_usd: Optional[float]
    new_balance_minor: Optional[int]
    new_balance_usd: Optional[float]
    receipt_id: int
    already_processed: Optional[bool] = False
