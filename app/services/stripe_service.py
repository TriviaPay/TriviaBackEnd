"""
Stripe Service - Handles Stripe Connect account creation and payouts
"""

import logging
import os
from typing import Dict, Optional

import stripe
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

logger = logging.getLogger(__name__)

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_API_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")

if not stripe.api_key:
    logger.warning("STRIPE_API_KEY not set - Stripe operations will fail")

if not STRIPE_WEBHOOK_SECRET:
    logger.warning("STRIPE_WEBHOOK_SECRET not set - Webhook verification will fail")


class StripeError(Exception):
    """Base exception for Stripe operations"""

    pass


class PayoutFailed(StripeError):
    """Exception raised when a payout fails"""

    pass


class AccountCreationFailed(StripeError):
    """Exception raised when account creation fails"""

    pass


async def create_or_get_connect_account(user: User) -> str:
    """
    Create or get Stripe Connect Express account for a user.

    Args:
        user: User model instance

    Returns:
        Stripe Connect account ID (acct_*)

    Raises:
        AccountCreationFailed: If account creation fails
    """
    # If user already has a connect account, return it
    if user.stripe_connect_account_id:
        return user.stripe_connect_account_id

    try:
        # Create Express account
        account = stripe.Account.create(
            type="express",
            country="US",  # Default to US, can be made configurable
            email=user.email,
            capabilities={
                "transfers": {"requested": True},
            },
        )

        account_id = account.id
        logger.info(
            f"Created Stripe Connect account {account_id} for user {user.account_id}"
        )

        return account_id

    except stripe.error.StripeError as e:
        logger.error(
            f"Failed to create Stripe Connect account for user {user.account_id}: {str(e)}"
        )
        raise AccountCreationFailed(
            f"Failed to create Stripe Connect account: {str(e)}"
        )


async def create_account_link(
    account_id: str, return_url: Optional[str] = None, refresh_url: Optional[str] = None
) -> Dict[str, str]:
    """
    Create account link for Stripe Connect onboarding.

    Args:
        account_id: Stripe Connect account ID
        return_url: URL to redirect to after onboarding (optional)
        refresh_url: URL to redirect to if link expires (optional)

    Returns:
        Dict with 'url' key containing the onboarding URL

    Raises:
        StripeError: If account link creation fails
    """
    try:
        if not return_url:
            return_url = os.getenv(
                "STRIPE_CONNECT_RETURN_URL",
                "https://app.triviapay.com/onboarding/return",
            )
        if not refresh_url:
            refresh_url = os.getenv(
                "STRIPE_CONNECT_REFRESH_URL",
                "https://app.triviapay.com/onboarding/refresh",
            )

        account_link = stripe.AccountLink.create(
            account=account_id,
            refresh_url=refresh_url,
            return_url=return_url,
            type="account_onboarding",
        )

        return {"url": account_link.url}

    except stripe.error.StripeError as e:
        logger.error(f"Failed to create account link for {account_id}: {str(e)}")
        raise StripeError(f"Failed to create account link: {str(e)}")


async def create_payout(
    connected_account_id: str,
    amount_minor: int,
    currency: str = "usd",
    description: Optional[str] = None,
) -> Dict[str, str]:
    """
    Create a payout to a connected account.

    Args:
        connected_account_id: Stripe Connect account ID
        amount_minor: Amount in minor units (cents)
        currency: Currency code (default: 'usd')
        description: Optional description for the payout

    Returns:
        Dict with 'payout_id' and 'status' keys

    Raises:
        PayoutFailed: If payout creation fails
    """
    try:
        payout = stripe.Transfer.create(
            amount=amount_minor,
            currency=currency,
            destination=connected_account_id,
            description=description or "Withdrawal payout",
        )

        logger.info(
            f"Created payout {payout.id} for account {connected_account_id}, amount: {amount_minor} {currency}"
        )

        return {
            "payout_id": payout.id,
            "status": payout.status,
            "amount": payout.amount,
            "currency": payout.currency,
        }

    except stripe.error.StripeError as e:
        logger.error(f"Failed to create payout for {connected_account_id}: {str(e)}")
        raise PayoutFailed(f"Failed to create payout: {str(e)}")


def verify_webhook_signature(payload: bytes, signature: str) -> stripe.Webhook:
    """
    Verify Stripe webhook signature.

    Args:
        payload: Raw request body as bytes
        signature: Stripe-Signature header value

    Returns:
        Stripe Webhook object if valid

    Raises:
        ValueError: If signature verification fails
    """
    if not STRIPE_WEBHOOK_SECRET:
        raise ValueError("STRIPE_WEBHOOK_SECRET not configured")

    try:
        # Construct event with timestamp tolerance (5 minutes default, configurable)
        # This prevents replay attacks by rejecting events that are too old
        timestamp_tolerance = int(
            os.getenv("STRIPE_WEBHOOK_TOLERANCE_SECONDS", "300")
        )  # Default 5 minutes
        event = stripe.Webhook.construct_event(
            payload, signature, STRIPE_WEBHOOK_SECRET, tolerance=timestamp_tolerance
        )
        return event
    except ValueError as e:
        logger.error(f"Invalid webhook payload: {str(e)}")
        raise ValueError(f"Invalid webhook payload: {str(e)}")
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid webhook signature: {str(e)}")
        raise ValueError(f"Invalid webhook signature: {str(e)}")


def get_publishable_key() -> str:
    """
    Get Stripe publishable key (for frontend/testing).

    Returns:
        Publishable key or empty string if not set
    """
    return STRIPE_PUBLISHABLE_KEY


async def get_or_create_stripe_customer_for_user(user: User, db: AsyncSession) -> str:
    """
    Get or create Stripe customer for a user.

    If user.stripe_customer_id exists, return it.
    Else create a Stripe customer with email=user.email and metadata={"account_id": user.account_id},
    save stripe_customer_id on user and commit, then return customer.id.

    Args:
        user: User model instance
        db: Async database session

    Returns:
        Stripe customer ID (cus_*)

    Raises:
        StripeError: If customer creation fails
    """
    # If user already has a customer ID, return it
    if user.stripe_customer_id:
        return user.stripe_customer_id

    try:
        # Create Stripe customer
        customer = stripe.Customer.create(
            email=user.email, metadata={"account_id": str(user.account_id)}
        )

        customer_id = customer.id
        logger.info(f"Created Stripe customer {customer_id} for user {user.account_id}")

        # Save customer ID to user
        user.stripe_customer_id = customer_id
        await db.commit()
        await db.refresh(user)

        return customer_id

    except stripe.error.StripeError as e:
        logger.error(
            f"Failed to create Stripe customer for user {user.account_id}: {str(e)}"
        )
        raise StripeError(f"Failed to create Stripe customer: {str(e)}")


def create_ephemeral_key_for_customer(
    customer_id: str, stripe_api_version: str = "2023-10-16"
) -> stripe.EphemeralKey:
    """
    Create ephemeral key for a Stripe customer.

    Ephemeral keys are used by mobile apps to securely make API calls to Stripe
    on behalf of the customer without exposing the secret key.

    Args:
        customer_id: Stripe customer ID
        stripe_api_version: Stripe API version to use (default: "2023-10-16")

    Returns:
        Stripe EphemeralKey object

    Raises:
        StripeError: If ephemeral key creation fails
    """
    try:
        ephemeral_key = stripe.EphemeralKey.create(
            customer=customer_id,
            stripe_version=stripe_api_version,
        )

        logger.info(f"Created ephemeral key for customer {customer_id}")
        return ephemeral_key

    except stripe.error.StripeError as e:
        logger.error(
            f"Failed to create ephemeral key for customer {customer_id}: {str(e)}"
        )
        raise StripeError(f"Failed to create ephemeral key: {str(e)}")


def create_payment_intent_for_topup(
    amount_minor: int,
    currency: str,
    user: User,
    topup_type: str,
    product_id: Optional[str] = None,
) -> stripe.PaymentIntent:
    """
    Create a PaymentIntent for wallet top-up or product purchase.

    Uses automatic_payment_methods.enabled=True to support cards, Apple Pay, and Google Pay
    automatically if enabled in Stripe Dashboard and frontend integration.

    Args:
        amount_minor: Amount in minor units (cents)
        currency: Currency code (e.g., 'usd')
        user: User model instance
        topup_type: Type of top-up ('wallet_topup' or 'product')
        product_id: Optional product ID for product purchases

    Returns:
        Stripe PaymentIntent object

    Raises:
        StripeError: If payment intent creation fails
    """
    try:
        payment_intent = stripe.PaymentIntent.create(
            amount=amount_minor,
            currency=currency,
            automatic_payment_methods={
                "enabled": True,
            },
            metadata={
                "account_id": str(user.account_id),
                "topup_type": topup_type,
                "product_id": product_id or "",
                "source": "app_payments",
            },
        )

        logger.info(
            f"Created PaymentIntent {payment_intent.id} for user {user.account_id}, "
            f"amount: {amount_minor} {currency}, type: {topup_type}"
        )

        return payment_intent

    except stripe.error.StripeError as e:
        logger.error(
            f"Failed to create PaymentIntent for user {user.account_id}: {str(e)}"
        )
        raise StripeError(f"Failed to create PaymentIntent: {str(e)}")
