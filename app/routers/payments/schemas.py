"""Payments/Wallet/IAP schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
#  Wallet
# ──────────────────────────────────────────────


class WalletTransactionResponse(BaseModel):
    """A single ledger entry in the user's wallet."""

    id: int = Field(..., description="Unique transaction ID", example=142)
    amount_minor: int = Field(
        ...,
        description="Signed amount in cents (positive = credit, negative = debit)",
        example=500,
    )
    amount_usd: float = Field(
        ..., description="Same amount expressed in USD", example=5.00
    )
    currency: str = Field(..., description="ISO 4217 currency code", example="usd")
    kind: str = Field(
        ...,
        description=(
            "Transaction kind. One of: "
            "deposit, withdraw, iap_credit, iap_refund, "
            "trivia_reward, adjustment, fee"
        ),
        example="trivia_reward",
    )
    created_at: Optional[str] = Field(
        None, description="ISO 8601 timestamp", example="2026-04-04T12:30:00"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "id": 142,
                "amount_minor": 500,
                "amount_usd": 5.00,
                "currency": "usd",
                "kind": "trivia_reward",
                "created_at": "2026-04-04T12:30:00",
            }
        }


class WalletBalanceResponse(BaseModel):
    """Current wallet balance with optional recent transactions."""

    balance_minor: int = Field(
        ..., description="Current balance in cents", example=1250
    )
    balance_usd: float = Field(
        ..., description="Current balance in USD", example=12.50
    )
    currency: str = Field(..., description="ISO 4217 currency code", example="usd")
    recent_transactions: Optional[List[WalletTransactionResponse]] = Field(
        None,
        description="Last 10 transactions (only included when include_transactions=true)",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "balance_minor": 1250,
                "balance_usd": 12.50,
                "currency": "usd",
                "recent_transactions": None,
            }
        }


class PaginatedTransactionsResponse(BaseModel):
    """Paginated list of wallet transactions."""

    transactions: List[WalletTransactionResponse] = Field(
        ..., description="List of transactions for the requested page"
    )
    total: int = Field(
        ..., description="Total number of transactions matching the filter", example=87
    )
    page: int = Field(..., description="Current page number (1-based)", example=1)
    page_size: int = Field(..., description="Number of items per page", example=20)

    class Config:
        json_schema_extra = {
            "example": {
                "transactions": [
                    {
                        "id": 142,
                        "amount_minor": 500,
                        "amount_usd": 5.00,
                        "currency": "usd",
                        "kind": "trivia_reward",
                        "created_at": "2026-04-04T12:30:00",
                    }
                ],
                "total": 87,
                "page": 1,
                "page_size": 20,
            }
        }


# ──────────────────────────────────────────────
#  Withdrawals
# ──────────────────────────────────────────────


class WithdrawalRequest(BaseModel):
    """Request a withdrawal from the wallet balance."""

    amount_usd: float = Field(
        ...,
        gt=0,
        description="Amount to withdraw in USD. Minimum $5.00.",
        example=10.00,
    )
    method: str = Field(
        ...,
        description="Withdrawal method. Accepted values: 'paypal', 'bank'",
        example="paypal",
    )
    details: Optional[str] = Field(
        None,
        description=(
            "Destination details. For 'paypal': the PayPal email address. "
            "For 'bank': routing + account number (format TBD)."
        ),
        example="user@example.com",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "amount_usd": 10.00,
                "method": "paypal",
                "details": "user@example.com",
            }
        }


class WithdrawalResponse(BaseModel):
    """A single withdrawal record."""

    id: int = Field(..., description="Unique withdrawal ID", example=7)
    amount: float = Field(..., description="Withdrawal amount in USD", example=10.00)
    withdrawal_method: str = Field(
        ..., description="Method used: 'paypal' or 'bank'", example="paypal"
    )
    withdrawal_status: str = Field(
        ...,
        description=(
            "Current status. One of: "
            "'requested' (pending review), "
            "'processing' (payout initiated), "
            "'completed' (funds sent), "
            "'failed' (payout failed — funds returned to wallet)"
        ),
        example="requested",
    )
    requested_at: Optional[str] = Field(
        None,
        description="ISO 8601 timestamp when the withdrawal was requested",
        example="2026-04-04T14:00:00",
    )
    processed_at: Optional[str] = Field(
        None,
        description="ISO 8601 timestamp when the withdrawal was processed (null if pending)",
        example=None,
    )

    class Config:
        json_schema_extra = {
            "example": {
                "id": 7,
                "amount": 10.00,
                "withdrawal_method": "paypal",
                "withdrawal_status": "requested",
                "requested_at": "2026-04-04T14:00:00",
                "processed_at": None,
            }
        }


class PaginatedWithdrawalsResponse(BaseModel):
    """Paginated list of withdrawal records."""

    withdrawals: List[WithdrawalResponse] = Field(
        ..., description="List of withdrawals for the requested page"
    )
    total: int = Field(
        ..., description="Total number of withdrawals for this user", example=3
    )
    page: int = Field(..., description="Current page number (1-based)", example=1)
    page_size: int = Field(..., description="Number of items per page", example=20)


# ──────────────────────────────────────────────
#  In-App Purchase (IAP)
# ──────────────────────────────────────────────


class AppleVerifyRequest(BaseModel):
    """Verify an Apple StoreKit 2 purchase.

    After a successful purchase on iOS, the client sends the
    signedTransactionInfo JWS string for server-side verification.
    The server decodes & validates the JWS, records the receipt,
    and credits the user's wallet or activates a subscription.
    """

    signed_transaction_info: str = Field(
        ...,
        description=(
            "The JWS (JSON Web Signature) string from StoreKit 2's "
            "Transaction.signedTransactionInfo. Must be a valid JWS "
            "signed by Apple."
        ),
        example="eyJhbGciOiJFUzI1NiIsIng1YyI6WyJNSUlFTURDQ0...",
    )
    product_id: str = Field(
        ...,
        description=(
            "Product ID as configured in App Store Connect. "
            "Examples: 'GP001' (gem package), 'AV001' (avatar), "
            "'SUB_BRONZE_MONTHLY' (subscription)."
        ),
        example="GP001",
    )
    app_account_token: Optional[str] = Field(
        default=None,
        description=(
            "Optional UUID set via appAccountToken in StoreKit 2. "
            "Used to bind the purchase to a specific user if the "
            "client sets it before initiating the purchase."
        ),
        example="550e8400-e29b-41d4-a716-446655440000",
    )
    environment: Optional[Literal["sandbox", "production"]] = Field(
        default="production",
        description=(
            "Apple environment. Use 'sandbox' during development/testing. "
            "Defaults to 'production'."
        ),
        example="production",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "signed_transaction_info": "eyJhbGciOiJFUzI1NiIsIng1YyI6WyJNSUlFTURDQ0...",
                "product_id": "GP001",
                "app_account_token": None,
                "environment": "production",
            }
        }


class GoogleVerifyRequest(BaseModel):
    """Verify a Google Play purchase.

    After a successful purchase on Android, the client sends the
    product_id and purchase_token. The server queries Google Play
    Developer API to validate, records the receipt, and credits
    the user's wallet or activates a subscription.
    """

    package_name: Optional[str] = Field(
        default=None,
        description=(
            "Android app package name (e.g., 'com.triviapay.app'). "
            "If omitted, falls back to the GOOGLE_IAP_PACKAGE_NAME "
            "environment variable."
        ),
        example="com.triviapay.app",
    )
    product_id: str = Field(
        ...,
        description=(
            "Product ID as configured in Google Play Console. "
            "Examples: 'GP001' (gem package), 'AV001' (avatar), "
            "'SUB_BRONZE_MONTHLY' (subscription)."
        ),
        example="GP001",
    )
    purchase_token: str = Field(
        ...,
        description=(
            "Opaque token provided by Google Play Billing Library "
            "after a successful purchase. Used to query Google for "
            "purchase validity."
        ),
        example="opaque-purchase-token-from-google",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "package_name": "com.triviapay.app",
                "product_id": "GP001",
                "purchase_token": "opaque-purchase-token-from-google",
            }
        }


class SubscriptionInfo(BaseModel):
    """Subscription activation details returned after a successful IAP
    that maps to a subscription plan."""

    subscription_id: int = Field(
        ..., description="Internal subscription record ID", example=3
    )
    plan_name: str = Field(
        ..., description="Human-readable plan name", example="Bronze Mode Monthly"
    )
    status: str = Field(
        ...,
        description="Subscription status: 'active', 'expired', 'cancelled'",
        example="active",
    )
    current_period_start: Optional[str] = Field(
        None,
        description="ISO 8601 start of the current billing period",
        example="2026-04-04T00:00:00",
    )
    current_period_end: Optional[str] = Field(
        None,
        description="ISO 8601 end of the current billing period",
        example="2026-05-04T00:00:00",
    )


class IapVerifyResponse(BaseModel):
    """Response from IAP verification (Apple or Google).

    On success the purchase is recorded and, if applicable,
    the user's wallet is credited or a subscription is activated.
    """

    success: bool = Field(
        ..., description="Whether the purchase was successfully verified", example=True
    )
    platform: str = Field(
        ..., description="'apple' or 'google'", example="apple"
    )
    transaction_id: str = Field(
        ...,
        description="Platform transaction ID (Apple transactionId or Google orderId)",
        example="2000000123456789",
    )
    product_id: str = Field(
        ..., description="Verified product ID", example="GP001"
    )
    credited_amount_minor: Optional[int] = Field(
        None,
        description="Amount credited to wallet in cents (null for subscriptions or non-consumables)",
        example=500,
    )
    credited_amount_usd: Optional[float] = Field(
        None, description="Same amount in USD", example=5.00
    )
    new_balance_minor: Optional[int] = Field(
        None, description="Updated wallet balance in cents after credit", example=1750
    )
    new_balance_usd: Optional[float] = Field(
        None, description="Updated wallet balance in USD", example=17.50
    )
    receipt_id: int = Field(
        ..., description="Internal receipt record ID for support reference", example=42
    )
    already_processed: Optional[bool] = Field(
        False,
        description=(
            "True if this transaction was already processed (duplicate). "
            "No double-credit occurs."
        ),
        example=False,
    )
    subscription: Optional[SubscriptionInfo] = Field(
        None,
        description="Populated when the purchase activated or renewed a subscription",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "platform": "apple",
                "transaction_id": "2000000123456789",
                "product_id": "GP001",
                "credited_amount_minor": 500,
                "credited_amount_usd": 5.00,
                "new_balance_minor": 1750,
                "new_balance_usd": 17.50,
                "receipt_id": 42,
                "already_processed": False,
                "subscription": None,
            }
        }
