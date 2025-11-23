"""
Stripe Service - Handles Stripe Connect account creation and payouts
"""
import logging
import stripe
import os
from typing import Optional, Dict
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
        logger.info(f"Created Stripe Connect account {account_id} for user {user.account_id}")
        
        return account_id
        
    except stripe.error.StripeError as e:
        logger.error(f"Failed to create Stripe Connect account for user {user.account_id}: {str(e)}")
        raise AccountCreationFailed(f"Failed to create Stripe Connect account: {str(e)}")


async def create_account_link(account_id: str, return_url: Optional[str] = None, refresh_url: Optional[str] = None) -> Dict[str, str]:
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
            return_url = os.getenv("STRIPE_CONNECT_RETURN_URL", "https://app.triviapay.com/onboarding/return")
        if not refresh_url:
            refresh_url = os.getenv("STRIPE_CONNECT_REFRESH_URL", "https://app.triviapay.com/onboarding/refresh")
        
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
    description: Optional[str] = None
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
            description=description or f"Withdrawal payout",
        )
        
        logger.info(f"Created payout {payout.id} for account {connected_account_id}, amount: {amount_minor} {currency}")
        
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
        event = stripe.Webhook.construct_event(
            payload, signature, STRIPE_WEBHOOK_SECRET
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

