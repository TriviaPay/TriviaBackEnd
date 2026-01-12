"""Payments/Wallet/IAP schemas."""

from datetime import datetime
from typing import List, Literal, Optional

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


class PaymentConfigResponse(BaseModel):
    publishable_key: str
    currency: str


class PaymentSheetInitRequest(BaseModel):
    amount_minor: Optional[int] = Field(
        None, gt=0, description="Amount in minor units (cents) for wallet top-up"
    )
    product_id: Optional[str] = Field(
        None, description="Product ID for product purchase (e.g., GP001, AV001)"
    )
    topup_type: Literal["wallet_topup", "product"] = Field(
        ..., description="Type of payment: wallet_topup or product"
    )
    currency: Optional[str] = Field("usd", description="Currency code (default: usd)")


class PaymentSheetResponse(BaseModel):
    customerId: str
    ephemeralKeySecret: str
    paymentIntentClientSecret: str
    amount_minor: int
    currency: str
    topup_type: str
    product_id: Optional[str] = None


class AccountLinkResponse(BaseModel):
    url: str
    account_id: str


class AppleVerifyRequest(BaseModel):
    receipt_data: str = Field(
        ..., description="Base64-encoded receipt data from StoreKit"
    )
    product_id: str = Field(
        ..., description="Product ID from the receipt (e.g., GP001, AV001)"
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


class WithdrawalResponse(BaseModel):
    id: int
    user_id: int
    username: Optional[str]
    email: Optional[str]
    amount_minor: int
    amount_usd: float
    currency: str
    type: str
    status: str
    fee_minor: int
    fee_usd: float
    stripe_payout_id: Optional[str]
    requested_at: datetime
    processed_at: Optional[datetime]
    admin_notes: Optional[str]
