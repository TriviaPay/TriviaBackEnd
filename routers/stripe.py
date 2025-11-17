"""
Stripe Integration Router
========================

This module provides a comprehensive integration with Stripe for payment processing, 
subscriptions, and wallet functionality within the TriviaBackEnd application.

Key Features:
-------------
1. Payment Processing:
   - Create payment intents for one-time payments
   - Process successful payments via webhooks
   - Handle failed payment scenarios

2. Wallet System:
   - Add funds to user wallet
   - Check wallet balance
   - View transaction history
   - Process withdrawals (with admin approval)

3. Bank Account Management:
   - Add, list, and delete bank accounts
   - Securely store bank information for withdrawals

4. Subscriptions:
   - Create and manage recurring subscriptions
   - Handle subscription lifecycle (creation, updates, cancellation)
   - Process subscription payments

5. Admin Functions:
   - Process withdrawal requests
   - Manage payment-related operations

Webhook Integration:
------------------
This router includes a webhook endpoint that handles Stripe events and updates the 
application state accordingly. The webhook endpoint should be configured in your 
Stripe dashboard to receive events.

Security Considerations:
----------------------
- Bank account information is encrypted before storage
- Only the last four digits of account numbers are stored in plain text
- Webhook signatures are verified to ensure authenticity of Stripe events
- Environment variables are used for Stripe API keys to prevent accidental exposure

For detailed information on using Stripe, see the official documentation:
https://stripe.com/docs/api
"""

import stripe
import logging
import json
import math
from decimal import Decimal
from fastapi import APIRouter, Depends, Request, HTTPException, BackgroundTasks, Header, Body, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from db import get_db
from models import User, Payment, PaymentTransaction, UserBankAccount, SubscriptionPlan, UserSubscription, WalletLedger, StripeWebhookEvent, WithdrawalRequest, UserWalletBalance
from utils.wallet_ledger import add_ledger_entry, get_balance, validate_currency
from routers.dependencies import get_current_user
import config
from datetime import datetime, timedelta
from utils.encryption import encrypt_data, decrypt_data, get_last_four
import os

# Initialize Stripe with the secret key
stripe.api_key = config.STRIPE_SECRET_KEY

router = APIRouter(prefix="/stripe", tags=["Stripe"])

logger = logging.getLogger(__name__)

# Pydantic models for request validation
class PaymentIntentRequest(BaseModel):
    """
    Request model for creating a payment intent with Stripe.
    """
    amount: int = Field(..., description="Amount in smallest currency unit (e.g., cents for USD). For example, use 1000 for $10.00.")
    currency: str = Field(default="usd", description="Three-letter ISO currency code (e.g., 'usd', 'eur', 'gbp'). Default is 'usd'.")
    metadata: Optional[Dict[str, str]] = Field(default=None, description="Additional information to attach to the payment, such as order ID, product details, etc.")
    
    @validator('amount')
    def amount_must_be_positive(cls, v):
        """Validate that amount is positive"""
        if v <= 0:
            raise ValueError('Amount must be greater than 0')
        return v
    
    @validator('currency')
    def currency_must_be_valid(cls, v):
        """Validate currency code format"""
        if not v or len(v) != 3:
            raise ValueError('Currency must be a 3-letter ISO code')
        return v.lower()

class BankAccountRequest(BaseModel):
    """
    Request model for adding a bank account for withdrawals.
    """
    account_holder_name: str = Field(..., description="Full name of the account holder as it appears on bank records")
    account_number: str = Field(..., description="Complete bank account number (will be encrypted in storage)")
    routing_number: str = Field(..., description="Bank routing number/sort code")
    bank_name: str = Field(..., description="Name of the bank (e.g., 'Chase', 'Bank of America')")
    is_default: bool = Field(False, description="Whether this should be set as the default account for withdrawals")
    
    @validator('account_number')
    def validate_account_number(cls, v):
        """Validate account number format"""
        if not v or len(v) < 8:
            raise ValueError('Account number must be at least 8 characters')
        return v
    
    @validator('routing_number')
    def validate_routing_number(cls, v):
        """Validate routing number format"""
        if not v or len(v) < 8:
            raise ValueError('Routing number must be at least 8 characters')
        return v

class BankAccountResponse(BaseModel):
    """
    Response model for bank account information.
    Only non-sensitive details are returned for security.
    """
    id: int = Field(..., description="Unique identifier for the bank account record")
    account_name: str = Field(..., description="Account holder name")
    account_number_last4: str = Field(..., description="Last 4 digits of the account number (for identification)")
    bank_name: str = Field(..., description="Name of the bank")
    is_default: bool = Field(..., description="Whether this is the default account for withdrawals")
    is_verified: bool = Field(..., description="Whether the account has been verified")
    created_at: datetime = Field(..., description="When the account was added to the system")
    
    class Config:
        from_attributes = True

# Endpoint to get the Stripe publishable key
@router.get("/public-key")
async def get_publishable_key():
    """
    ## Get Stripe Publishable Key
    
    Retrieves the Stripe publishable key required for client-side Stripe integration.
    
    ### Use this endpoint to:
    - Initialize the Stripe.js library on the client-side
    - Set up Elements components like Card elements or Payment Elements
    
    ### Returns:
    - `publishableKey`: The Stripe publishable key for your environment (test or live)
    """
    return {"publishableKey": config.STRIPE_PUBLISHABLE_KEY}

# Endpoint to get user's payment methods
@router.get("/payment-methods")
async def get_payment_methods(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    ## Get User's Payment Methods
    
    Retrieves all payment methods associated with the authenticated user.
    
    ### Use this endpoint to:
    - Display saved payment methods to users
    - Allow users to select from existing payment methods
    - Show payment method details for subscription billing
    
    ### Returns:
    - List of payment methods with the following information:
        - `id`: Payment method ID
        - `type`: Type of payment method (card, bank_account, etc.)
        - `card`: Card details (if applicable)
        - `billing_details`: Billing address information
        - `created`: When the payment method was created
        - `customer`: Customer ID (if attached to a customer)
    
    ### Note:
    This endpoint requires the user to have a Stripe customer ID. If the user
    doesn't have one, an empty list will be returned.
    """
    try:
        # Get user from the database
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Check if user has a Stripe customer ID
        if not user.stripe_customer_id:
            # User doesn't have any payment methods yet
            return {"payment_methods": []}
        
        # Retrieve payment methods from Stripe
        payment_methods = stripe.PaymentMethod.list(
            customer=user.stripe_customer_id,
            type='card'
        )
        
        # Format the response
        formatted_methods = []
        for method in payment_methods.data:
            formatted_method = {
                "id": method.id,
                "type": method.type,
                "created": method.created,
                "livemode": method.livemode
            }
            
            # Add card details if it's a card payment method
            if method.type == 'card' and method.card:
                formatted_method["card"] = {
                    "brand": method.card.brand,
                    "country": method.card.country,
                    "exp_month": method.card.exp_month,
                    "exp_year": method.card.exp_year,
                    "fingerprint": method.card.fingerprint,
                    "funding": method.card.funding,
                    "generated_from": method.card.generated_from,
                    "last4": method.card.last4,
                    "networks": method.card.networks,
                    "three_d_secure_usage": method.card.three_d_secure_usage,
                    "wallet": method.card.wallet
                }
            
            # Add billing details if available
            if method.billing_details:
                formatted_method["billing_details"] = {
                    "address": method.billing_details.address,
                    "email": method.billing_details.email,
                    "name": method.billing_details.name,
                    "phone": method.billing_details.phone
                }
            
            formatted_methods.append(formatted_method)
        
        return {"payment_methods": formatted_methods}
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error retrieving payment methods: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error retrieving payment methods: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint to create a Stripe customer
@router.post("/create-customer")
async def create_stripe_customer(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    ## Create Stripe Customer
    
    Creates a Stripe customer for the authenticated user to enable payment method storage.
    
    ### Use this endpoint to:
    - Initialize a user's Stripe customer account
    - Enable saving payment methods for future use
    - Set up subscription billing capabilities
    
    ### Returns:
    - `customer_id`: The Stripe customer ID
    - `message`: Success message
    
    ### Note:
    This endpoint should be called before attempting to save payment methods.
    If the user already has a Stripe customer ID, it will return the existing one.
    """
    try:
        # Get user from the database
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Check if user already has a Stripe customer ID
        if user.stripe_customer_id:
            return {
                "customer_id": user.stripe_customer_id,
                "message": "User already has a Stripe customer account"
            }
        
        # Create a new Stripe customer
        customer = stripe.Customer.create(
            email=user.email,
            name=f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username,
            metadata={
                "user_id": str(user.account_id),
                "sub": sub,
                "username": user.username
            }
        )
        
        # Update the user record with the Stripe customer ID
        user.stripe_customer_id = customer.id
        db.add(user)
        db.commit()
        
        return {
            "customer_id": customer.id,
            "message": "Stripe customer account created successfully"
        }
        
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error creating customer: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating Stripe customer: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint to add funds to the user's wallet
@router.post("/add-funds-to-wallet")
async def add_funds_to_wallet(
    amount_minor: int = Body(..., description="Amount in minor units (cents) to add to wallet (e.g., 1000 for $10.00)"),
    currency: str = Body("usd", description="Three-letter currency code (e.g., 'usd', 'eur')"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    ## Add Funds to User Wallet
    
    Creates a PaymentIntent and returns the client secret needed to complete payment on the client side.
    
    ### Use this endpoint to:
    - Allow users to top up their wallet balance
    - Process payments via Stripe that will be credited to the user's in-app wallet
    
    ### Request Body:
    - `amount_minor`: The amount in minor units (cents) to add to the wallet (e.g., 1000 for $10.00)
    - `currency`: Three-letter currency code (default: 'usd')
    
    ### Returns:
    - `clientSecret`: Stripe PaymentIntent client secret to complete payment on the client
    - `paymentIntentId`: ID of the created Stripe PaymentIntent
    - `amount_minor`: The amount in minor units that will be charged
    - `currency`: The currency code being used
    
    ### Note:
    The wallet will only be credited once the payment is confirmed via webhook.
    """
    try:
        # Get user from the database
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Validate currency
        if not validate_currency(currency):
            raise HTTPException(status_code=400, detail=f"Invalid currency code: {currency}")
        
        # Validate amount
        if amount_minor <= 0:
            raise HTTPException(status_code=400, detail="Amount must be greater than 0")
        
        # Generate idempotency key
        idempotency_key = f"wallet_deposit_{user.account_id}_{int(datetime.utcnow().timestamp())}"
        
        # Create payment intent with user metadata
        metadata = {
            "transaction_type": "wallet_deposit",
            "user_id": str(user.account_id),
            "sub": sub
        }
        
        payment_intent = stripe.PaymentIntent.create(
            amount=amount_minor,
            currency=currency,
            metadata=metadata,
            automatic_payment_methods={"enabled": True}
        )
        
        # Create a transaction record in pending state
        transaction = PaymentTransaction(
            user_id=user.account_id,
            payment_intent_id=payment_intent.id,
            amount=amount_minor / 100.0,  # Keep for backward compatibility
            amount_minor=amount_minor,
            currency=currency,
            status="pending",
            direction="inbound",
            payment_metadata=json.dumps(metadata),
            idempotency_key=idempotency_key,
            livemode=payment_intent.livemode if hasattr(payment_intent, 'livemode') else False
        )
        
        db.add(transaction)
        db.commit()
        
        return {
            "clientSecret": payment_intent.client_secret,
            "paymentIntentId": payment_intent.id,
            "amount_minor": amount_minor,
            "currency": currency
        }
    except stripe.error.StripeError as e:
        # Log and return error
        logger.error(f"Stripe error: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        # Log and return error
        logger.error(f"Error adding funds to wallet: {str(e)}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint to create a payment intent
@router.post("/create-payment-intent")
async def create_payment_intent(
    amount: int = Body(..., description="Amount in cents to charge (e.g., 1000 for $10.00)"),
    currency: str = Body("usd", description="Three-letter currency code (e.g., 'usd', 'eur')"),
    transaction_type: str = Body(None, description="Type of transaction (e.g., 'purchase', 'subscription', 'donation')"),
    product_id: Optional[str] = Body(None, description="ID of the product being purchased"),
    order_id: Optional[str] = Body(None, description="Reference ID for the order"),
    description: Optional[str] = Body(None, description="Description of what is being purchased"),
    customer_reference: Optional[str] = Body(None, description="Customer-provided reference or note"),
    additional_metadata: Optional[Dict[str, str]] = Body({}, description="Any additional metadata to attach to the payment intent"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    ## Create Generic Payment Intent
    
    Creates a Stripe PaymentIntent for generic payments not specifically tied to wallet funding.
    
    ### Use this endpoint to:
    - Process one-time payments for goods or services
    - Handle custom payment flows that don't fit into predefined categories
    
    ### Request Body:
    - `amount`: Amount in cents to charge (e.g., 1000 for $10.00)
    - `currency`: Three-letter currency code (default: 'usd')
    - `transaction_type`: Optional type of transaction (e.g., 'purchase', 'subscription', 'donation')
    - `product_id`: Optional ID of the product being purchased
    - `order_id`: Optional reference ID for the order
    - `description`: Optional description of what is being purchased
    - `customer_reference`: Optional customer-provided reference or note
    - `additional_metadata`: Optional additional information to store with the payment
    
    ### Returns:
    - `clientSecret`: Stripe PaymentIntent client secret needed to complete payment on the client
    
    ### Note:
    This endpoint creates a basic payment intent. Use the webhook to handle the payment completion.
    """
    try:
        # Get user ID from token
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Prepare metadata from the specific fields
        metadata = {}
        
        # Add user information
        metadata["user_id"] = str(user.account_id)
        metadata["sub"] = sub
        
        # Add provided fields if they exist
        if transaction_type:
            metadata["transaction_type"] = transaction_type
        if product_id:
            metadata["product_id"] = product_id
        if order_id:
            metadata["order_id"] = order_id
        if description:
            metadata["description"] = description
        if customer_reference:
            metadata["customer_reference"] = customer_reference
            
        # Add any additional metadata
        if additional_metadata:
            for key, value in additional_metadata.items():
                # Prevent overwriting core metadata
                if key not in ["user_id", "sub", "transaction_type", "product_id", "order_id", "description", "customer_reference"]:
                    metadata[key] = value
        
        # Create a payment intent
        payment_intent = stripe.PaymentIntent.create(
            amount=amount,
            currency=currency,
            metadata=metadata,
            automatic_payment_methods={"enabled": True}
        )
        
        return {"clientSecret": payment_intent.client_secret}
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating payment intent: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Function to handle successful payment
async def handle_successful_payment(payment_intent, event_id: Optional[str] = None):
    # Create a database session
    db = next(get_db())
    
    try:
        # Find transaction by payment intent ID
        transaction = db.query(PaymentTransaction).filter(
            PaymentTransaction.payment_intent_id == payment_intent.id
        ).first()
        
        metadata = payment_intent.metadata.to_dict() if hasattr(payment_intent.metadata, 'to_dict') else payment_intent.metadata
        user_id = int(metadata.get("user_id")) if metadata.get("user_id") else None
        currency = payment_intent.currency or 'usd'
        amount_minor = payment_intent.amount  # Already in minor units from Stripe
        livemode = payment_intent.livemode if hasattr(payment_intent, 'livemode') else False
        
        if transaction:
            # Check if the transaction is already marked as succeeded (idempotency check)
            if transaction.status == "succeeded":
                logger.info(f"Payment {payment_intent.id} already processed. Skipping to ensure idempotency.")
                return
                
            # Update transaction status
            transaction.status = "succeeded"
            transaction.amount_minor = amount_minor
            transaction.payment_method = payment_intent.payment_method
            transaction.payment_method_type = payment_intent.payment_method_types[0] if payment_intent.payment_method_types else None
            transaction.payment_metadata = json.dumps(metadata)
            transaction.event_id = event_id
            transaction.livemode = livemode
            transaction.stripe_customer_id = payment_intent.customer if hasattr(payment_intent, 'customer') else None
            transaction.charge_id = payment_intent.latest_charge if hasattr(payment_intent, 'latest_charge') else None
            
            # Get user from transaction
            user = db.query(User).filter(User.account_id == transaction.user_id).first()
            
            if user and metadata.get("transaction_type") == "wallet_deposit":
                # Check per-object idempotency: ensure only one deposit per payment_intent
                existing_ledger = db.query(WalletLedger).filter(
                    WalletLedger.external_ref_type == 'payment_intent',
                    WalletLedger.external_ref_id == payment_intent.id,
                    WalletLedger.kind == 'deposit'
                ).first()
                
                if existing_ledger:
                    logger.info(f"Deposit for payment_intent {payment_intent.id} already processed. Balance: {existing_ledger.balance_after_minor}")
                    return
                
                # Use wallet ledger to add funds atomically
                try:
                    new_balance = add_ledger_entry(
                        db=db,
                        user_id=transaction.user_id,
                        currency=currency,
                        delta_minor=amount_minor,
                        kind='deposit',
                        external_ref_type='payment_intent',
                        external_ref_id=payment_intent.id,
                        event_id=event_id,
                        idempotency_key=f"pi_{payment_intent.id}",
                        livemode=livemode
                    )
                    logger.info(f"Added {amount_minor} {currency} to user {transaction.user_id}'s wallet. New balance: {new_balance}")
                except ValueError as e:
                    # Idempotency check - already processed
                    logger.info(f"Wallet entry already exists for payment {payment_intent.id}: {str(e)}")
            
            db.commit()
            logger.info(f"Payment successful for transaction {transaction.id}")
        else:
            # Create a new transaction record if not found
            if user_id:
                transaction = PaymentTransaction(
                    user_id=user_id,
                    payment_intent_id=payment_intent.id,
                    amount=amount_minor / 100.0,  # Keep for backward compatibility
                    amount_minor=amount_minor,
                    currency=currency,
                    status="succeeded",
                    payment_method=payment_intent.payment_method,
                    payment_method_type=payment_intent.payment_method_types[0] if payment_intent.payment_method_types else None,
                    payment_metadata=json.dumps(metadata),
                    event_id=event_id,
                    livemode=livemode,
                    stripe_customer_id=payment_intent.customer if hasattr(payment_intent, 'customer') else None,
                    charge_id=payment_intent.latest_charge if hasattr(payment_intent, 'latest_charge') else None,
                    direction='inbound'
                )
                
                db.add(transaction)
                
                # If this is a wallet deposit, add funds to user's wallet using ledger
                if metadata.get("transaction_type") == "wallet_deposit":
                    # Check per-object idempotency: ensure only one deposit per payment_intent
                    existing_ledger = db.query(WalletLedger).filter(
                        WalletLedger.external_ref_type == 'payment_intent',
                        WalletLedger.external_ref_id == payment_intent.id,
                        WalletLedger.kind == 'deposit'
                        ).first()
                        
                    if existing_ledger:
                        logger.info(f"Deposit for payment_intent {payment_intent.id} already processed. Balance: {existing_ledger.balance_after_minor}")
                    else:
                        try:
                            new_balance = add_ledger_entry(
                                db=db,
                                user_id=user_id,
                                currency=currency,
                                delta_minor=amount_minor,
                                kind='deposit',
                                external_ref_type='payment_intent',
                                external_ref_id=payment_intent.id,
                                event_id=event_id,
                                idempotency_key=f"pi_{payment_intent.id}",
                                livemode=livemode
                            )
                            logger.info(f"Added {amount_minor} {currency} to user {user_id}'s wallet. New balance: {new_balance}")
                        except ValueError as e:
                            logger.warning(f"Could not add wallet entry: {str(e)}")
                
                db.commit()
                logger.info(f"Created new transaction record for payment {payment_intent.id}")
    except Exception as e:
        db.rollback()
        logger.error(f"Error processing successful payment: {str(e)}", exc_info=True)
    finally:
        db.close()

# Function to handle refund
async def handle_refund(charge, event_id: Optional[str] = None):
    """Handle charge refund events."""
    db = next(get_db())
    try:
        # Find transaction by charge ID
        transaction = db.query(PaymentTransaction).filter(
            PaymentTransaction.charge_id == charge.id
        ).first()
        
        if not transaction:
            logger.warning(f"Transaction not found for charge {charge.id}")
            return
        
        # Check if already processed
        if transaction.refund_id:
            logger.info(f"Refund for charge {charge.id} already processed")
            return
        
        refund_amount_minor = charge.amount_refunded if hasattr(charge, 'amount_refunded') else 0
        currency = charge.currency or transaction.currency
        livemode = charge.livemode if hasattr(charge, 'livemode') else False
        
        # Update transaction
        if hasattr(charge, 'refunds') and charge.refunds and charge.refunds.data:
            transaction.refund_id = charge.refunds.data[0].id
        else:
            transaction.refund_id = charge.id
        
        # If this was a wallet deposit, debit from wallet
        metadata = json.loads(transaction.payment_metadata) if transaction.payment_metadata else {}
        if metadata.get("transaction_type") == "wallet_deposit" and refund_amount_minor > 0:
            try:
                new_balance = add_ledger_entry(
                    db=db,
                    user_id=transaction.user_id,
                    currency=currency,
                    delta_minor=-refund_amount_minor,  # Negative for refund
                    kind='refund',
                    external_ref_type='charge',
                    external_ref_id=charge.id,
                    event_id=event_id,
                    idempotency_key=f"refund_{charge.id}",
                    livemode=livemode
                )
                logger.info(f"Refunded {refund_amount_minor} {currency} from user {transaction.user_id}'s wallet. New balance: {new_balance}")
            except ValueError as e:
                logger.warning(f"Could not process refund: {str(e)}")
                
                db.commit()
        logger.info(f"Refund processed for charge {charge.id}")
    except Exception as e:
        db.rollback()
        logger.error(f"Error processing refund: {str(e)}", exc_info=True)
    finally:
        db.close()


# Function to handle dispute
async def handle_dispute(dispute, event_id: Optional[str] = None):
    """Handle charge dispute events."""
    db = next(get_db())
    try:
        charge_id = dispute.charge if isinstance(dispute.charge, str) else dispute.charge.id
        transaction = db.query(PaymentTransaction).filter(
            PaymentTransaction.charge_id == charge_id
        ).first()
        
        if not transaction:
            logger.warning(f"Transaction not found for charge {charge_id}")
            return
        
        dispute_amount_minor = dispute.amount
        currency = dispute.currency or transaction.currency
        livemode = dispute.livemode if hasattr(dispute, 'livemode') else False
        
        metadata = json.loads(transaction.payment_metadata) if transaction.payment_metadata else {}
        
        if dispute.status == 'warning_needs_response' or dispute.status == 'needs_response':
            # Hold funds
            try:
                new_balance = add_ledger_entry(
                    db=db,
                    user_id=transaction.user_id,
                    currency=currency,
                    delta_minor=-dispute_amount_minor,
                    kind='dispute_hold',
                    external_ref_type='dispute',
                    external_ref_id=dispute.id,
                    event_id=event_id,
                    idempotency_key=f"dispute_hold_{dispute.id}",
                    livemode=livemode
                )
                logger.info(f"Dispute hold: {dispute_amount_minor} {currency} from user {transaction.user_id}. New balance: {new_balance}")
            except ValueError as e:
                logger.warning(f"Could not process dispute hold: {str(e)}")
        
        elif dispute.status == 'won':
            # Release hold
            try:
                new_balance = add_ledger_entry(
                    db=db,
                    user_id=transaction.user_id,
                    currency=currency,
                    delta_minor=dispute_amount_minor,
                    kind='dispute_release',
                    external_ref_type='dispute',
                    external_ref_id=dispute.id,
                    event_id=event_id,
                    idempotency_key=f"dispute_release_{dispute.id}",
                    livemode=livemode
                )
                logger.info(f"Dispute won: released {dispute_amount_minor} {currency} to user {transaction.user_id}. New balance: {new_balance}")
            except ValueError as e:
                logger.warning(f"Could not process dispute release: {str(e)}")
        
        elif dispute.status == 'lost':
            # Funds are debited (already held, no additional action needed)
            logger.info(f"Dispute lost for charge {charge_id}, funds already held")
        
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error processing dispute: {str(e)}", exc_info=True)
    finally:
        db.close()


# Function to handle failed payment
async def handle_failed_payment(payment_intent_id: str, db: Session):
    """
    Process a failed payment in the background.
    Updates database records, sends notification emails, etc.
    """
    try:
        # Retrieve the payment intent to get all details
        payment_intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        
        # Extract user information from metadata
        user_id = payment_intent.metadata.get("user_id")
        if not user_id:
            logger.error(f"No user_id found in payment_intent metadata: {payment_intent_id}")
            return
        
        # Find or create a transaction record
        transaction = db.query(PaymentTransaction).filter(
            PaymentTransaction.payment_intent_id == payment_intent_id
        ).first()
        
        if not transaction:
            # Create a new transaction record if one doesn't exist
            transaction = PaymentTransaction(
                user_id=user_id,
                payment_intent_id=payment_intent_id,
                amount=payment_intent.amount / 100.0,  # Convert cents to dollars
                currency=payment_intent.currency,
                status='failed',
                last_error=payment_intent.last_payment_error.message if payment_intent.last_payment_error else None,
                payment_metadata=json.dumps(payment_intent.metadata.to_dict())
            )
            db.add(transaction)
        else:
            # Update existing transaction
            transaction.status = 'failed'
            transaction.last_error = payment_intent.last_payment_error.message if payment_intent.last_payment_error else None
        
        # Commit the changes
        db.commit()
        
        # Log the failed payment
        logger.info(f"Payment failed: {payment_intent_id} for user {user_id}")
        
    except Exception as e:
        logger.error(f"Error processing failed payment: {str(e)}")
        db.rollback()

# Endpoint to handle Stripe webhooks
@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    stripe_signature: str = Header(None),
    db: Session = Depends(get_db)
):
    """
    ## Stripe Webhook Endpoint
    
    Handles incoming Stripe events such as successful payments, failed charges, subscription updates, etc.
    
    ### Use this endpoint to:
    - Receive real-time notifications from Stripe when payment states change
    - Process successful payments to update user's wallet or activate subscriptions
    - Handle failed payments
    
    ### Required Headers:
    - `stripe-signature`: Signature provided by Stripe to verify the webhook
    
    ### Events Handled:
    - `payment_intent.succeeded`: When a payment is successfully completed
    - `payment_intent.payment_failed`: When a payment attempt fails
    - `customer.subscription.created/updated/deleted`: Subscription lifecycle events
    - `invoice.payment_succeeded/payment_failed`: Subscription billing events
    - `invoice.payment_action_required`: When user action is needed to complete payment
    - `payout.created/paid/failed`: Withdrawal payout events
    - `bank_account.verified/verification_failed`: Bank account verification events
    
    ### Note:
    This endpoint should be configured in your Stripe dashboard as a webhook endpoint.
    The webhook secret should be set in your environment variables.
    
    ### Future Improvements:
    - Implement an audit_log table to track all payment-related events for compliance
    - Add notification system for critical events like failed payouts
    """
    event_id = None  # Initialize event_id
    try:
        # Get the raw payload BEFORE any JSON parsing (critical for signature verification)
        payload = await request.body()
        sig_header = stripe_signature
        
        logger.info("Received Stripe webhook")
        
        # Check event idempotency first (before processing)
        try:
            # Parse JSON only for event ID check, but keep raw payload for signature
            payload_json = json.loads(payload)
            event_id = payload_json.get('id')
            
            if event_id:
                # Check if we've already processed this event
                existing_event = db.query(StripeWebhookEvent).filter(
                    StripeWebhookEvent.event_id == event_id
                ).first()
                
                if existing_event:
                    if existing_event.status == 'processed':
                        logger.info(f"Event {event_id} already processed, skipping")
                        return {"status": "success", "message": "Event already processed"}
                    elif existing_event.status == 'failed':
                        logger.warning(f"Event {event_id} previously failed, retrying")
                    else:
                        logger.info(f"Event {event_id} already received, processing")
        except json.JSONDecodeError:
            logger.warning("Could not parse payload for event ID check, continuing...")
        
        try:
            # Verify webhook signature using RAW body with timestamp tolerance (5 minutes = 300 seconds)
            endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
            if endpoint_secret:
                event = stripe.Webhook.construct_event(
                    payload, sig_header, endpoint_secret, tolerance=300
                )
            else:
                # For testing without a webhook secret
                data = json.loads(payload)
                event = stripe.Event.construct_from(data, stripe.api_key)
                
            # Get event_id from event object if not already set
            if not event_id and hasattr(event, 'id'):
                event_id = event.id
            
            # Store event in database for idempotency tracking
            if event_id:
                webhook_event = db.query(StripeWebhookEvent).filter(
                    StripeWebhookEvent.event_id == event_id
                ).first()
                
                if not webhook_event:
                    webhook_event = StripeWebhookEvent(
                        event_id=event_id,
                        type=event.type,
                        livemode=event.livemode,
                        status='received'
                    )
                    db.add(webhook_event)
                    db.commit()
                else:
                    webhook_event.received_at = datetime.utcnow()
                    db.add(webhook_event)
                    db.commit()
                
            logger.info(f"Validated webhook: {event.type}, ID: {event_id}")
        except (ValueError, stripe.error.SignatureVerificationError) as e:
            logger.error(f"Webhook signature verification failed: {e}")
            if event_id:
                webhook_event = db.query(StripeWebhookEvent).filter(
                    StripeWebhookEvent.event_id == event_id
                ).first()
                if webhook_event:
                    webhook_event.status = 'failed'
                    webhook_event.last_error = str(e)
                    db.add(webhook_event)
                    db.commit()
            raise HTTPException(status_code=400, detail=str(e))
        
        # Handle the event
        event_type = event.type
        event_object = event.data.object
        
        # Payment Intent events
        if event_type.startswith('payment_intent.'):
            payment_intent_id = event_object.id
            logger.info(f"Processing payment_intent event: {event_type}, ID: {payment_intent_id}")
            
            if event_type == 'payment_intent.succeeded':
                # Handle payment succeeded
                background_tasks.add_task(handle_successful_payment, event_object, event.id)
                
            elif event_type == 'payment_intent.payment_failed':
                # Handle payment failed
                background_tasks.add_task(handle_failed_payment, payment_intent_id, db)
                
            elif event_type == 'payment_intent.canceled':
                # Handle payment canceled
                background_tasks.add_task(handle_failed_payment, payment_intent_id, db)
        
        # Subscription events
        elif event_type.startswith('customer.subscription.'):
            subscription_id = event_object.id
            logger.info(f"Processing subscription event: {event_type}, ID: {subscription_id}")
            
            # Find the user subscription by Stripe ID
            user_sub = db.query(UserSubscription).filter(
                UserSubscription.stripe_subscription_id == subscription_id
            ).first()
            
            if user_sub:
                if event_type == 'customer.subscription.created':
                    user_sub.status = event_object.status
                    user_sub.current_period_start = datetime.fromtimestamp(event_object.current_period_start)
                    user_sub.current_period_end = datetime.fromtimestamp(event_object.current_period_end)
                    user_sub.stripe_customer_id = event_object.customer if hasattr(event_object, 'customer') else user_sub.stripe_customer_id
                    user_sub.default_payment_method_id = event_object.default_payment_method if hasattr(event_object, 'default_payment_method') else user_sub.default_payment_method_id
                    user_sub.livemode = event_object.livemode if hasattr(event_object, 'livemode') else user_sub.livemode
                    db.add(user_sub)
                    db.commit()
                
                elif event_type == 'customer.subscription.updated':
                    user_sub.status = event_object.status
                    user_sub.current_period_start = datetime.fromtimestamp(event_object.current_period_start)
                    user_sub.current_period_end = datetime.fromtimestamp(event_object.current_period_end)
                    user_sub.cancel_at_period_end = event_object.cancel_at_period_end
                    if hasattr(event_object, 'cancel_at') and event_object.cancel_at:
                        user_sub.cancel_at = datetime.fromtimestamp(event_object.cancel_at)
                    if hasattr(event_object, 'canceled_at') and event_object.canceled_at:
                        user_sub.canceled_at = datetime.fromtimestamp(event_object.canceled_at)
                    user_sub.default_payment_method_id = event_object.default_payment_method if hasattr(event_object, 'default_payment_method') else user_sub.default_payment_method_id
                    db.add(user_sub)
                    db.commit()
                
                elif event_type == 'customer.subscription.deleted':
                    user_sub.status = 'canceled'
                    if hasattr(event_object, 'canceled_at') and event_object.canceled_at:
                        user_sub.canceled_at = datetime.fromtimestamp(event_object.canceled_at)
                    db.add(user_sub)
                    db.commit()
        
        # Invoice events for subscription billing
        elif event_type.startswith('invoice.'):
            invoice_id = event_object.id
            subscription_id = event_object.subscription
            customer_id = event_object.customer
            
            logger.info(f"Processing invoice event: {event_type}, ID: {invoice_id}, Subscription: {subscription_id}")
            
            if subscription_id and event_type == 'invoice.payment_succeeded':
                # Find the user subscription
                user_sub = db.query(UserSubscription).filter(
                    UserSubscription.stripe_subscription_id == subscription_id
                ).first()
                
                if user_sub:
                    # Idempotency check - see if we already recorded this invoice payment
                    existing_transaction = db.query(PaymentTransaction).filter(
                        PaymentTransaction.payment_metadata.contains(f'"invoice_id": "{invoice_id}"')
                    ).first()
                    
                    if existing_transaction:
                        logger.info(f"Invoice {invoice_id} already processed. Skipping to ensure idempotency.")
                    else:
                        # Create a payment transaction record
                        amount_paid_minor = event_object.amount_paid  # Already in minor units from Stripe
                        transaction = PaymentTransaction(
                            user_id=user_sub.user_id,
                            payment_intent_id=event_object.payment_intent,
                            amount=amount_paid_minor / 100.0,  # Keep for backward compatibility
                            amount_minor=amount_paid_minor,
                            currency=event_object.currency,
                            status='succeeded',
                            payment_method='card',
                            payment_method_type='subscription',
                            direction='subscription',
                            event_id=event_id,
                            livemode=event_object.livemode if hasattr(event_object, 'livemode') else False,
                            stripe_customer_id=customer_id,
                            payment_metadata=json.dumps({
                                'transaction_type': 'subscription_renewal',
                                'invoice_id': invoice_id,
                                'subscription_id': subscription_id,
                                'user_id': str(user_sub.user_id)
                            })
                        )
                        db.add(transaction)
                        
                        # Update subscription fields
                        user_sub.status = 'active'
                        user_sub.latest_invoice_id = invoice_id
                        if hasattr(event_object, 'period_start'):
                            user_sub.current_period_start = datetime.fromtimestamp(event_object.period_start)
                        if hasattr(event_object, 'period_end'):
                            user_sub.current_period_end = datetime.fromtimestamp(event_object.period_end)
                        db.add(user_sub)
                        db.commit()
            
            elif subscription_id and event_type == 'invoice.payment_failed':
                # Find the user subscription
                user_sub = db.query(UserSubscription).filter(
                    UserSubscription.stripe_subscription_id == subscription_id
                ).first()
                
                if user_sub:
                    # Update subscription status
                    user_sub.status = 'past_due'
                    db.add(user_sub)
                    db.commit()
            
            elif subscription_id and event_type == 'invoice.payment_action_required':
                # User needs to take action to complete payment
                user_sub = db.query(UserSubscription).filter(
                    UserSubscription.stripe_subscription_id == subscription_id
                ).first()
                
                if user_sub and user_sub.user_id:
                    # Get the user
                    user = db.query(User).filter(User.account_id == user_sub.user_id).first()
                    if user:
                        # Update subscription status
                        user_sub.status = 'incomplete'
                        db.add(user_sub)
                        
                        # Track the action required in the database
                        action_url = event_object.hosted_invoice_url
                        
                        # Store the payment action required info
                        amount_due_minor = event_object.amount_due  # Already in minor units
                        transaction = PaymentTransaction(
                            user_id=user_sub.user_id,
                            payment_intent_id=event_object.payment_intent,
                            amount=amount_due_minor / 100.0,  # Keep for backward compatibility
                            amount_minor=amount_due_minor,
                            currency=event_object.currency,
                            status='action_required',
                            payment_method='card',
                            payment_method_type='subscription',
                            direction='subscription',
                            event_id=event_id,
                            livemode=event_object.livemode if hasattr(event_object, 'livemode') else False,
                            stripe_customer_id=customer_id,
                            payment_metadata=json.dumps({
                                'transaction_type': 'subscription_renewal',
                                'invoice_id': invoice_id,
                                'subscription_id': subscription_id,
                                'user_id': str(user_sub.user_id),
                                'action_required': True,
                                'action_url': action_url
                            })
                        )
                        db.add(transaction)
                        db.commit()
                        
                        # TODO: Send notification to user about required action
                        logger.info(f"Payment action required for subscription {subscription_id}, user {user_sub.user_id}")
        
        # Payout events for withdrawals
        elif event_type.startswith('payout.'):
            payout_id = event_object.id
            logger.info(f"Processing payout event: {event_type}, ID: {payout_id}")
            
            # Find withdrawal request by payout ID
            withdrawal_request = db.query(WithdrawalRequest).filter(
                WithdrawalRequest.stripe_payout_id == payout_id
            ).first()
            
            if withdrawal_request:
                if event_type == 'payout.created':
                    # Update withdrawal request status
                    withdrawal_request.status = 'processing'
                    withdrawal_request.stripe_balance_txn_id = event_object.balance_transaction if hasattr(event_object, 'balance_transaction') else withdrawal_request.stripe_balance_txn_id
                    withdrawal_request.event_id = event_id
                    db.add(withdrawal_request)
                    db.commit()
                
                elif event_type == 'payout.paid':
                    # Payout successfully deposited
                    withdrawal_request.status = 'paid'
                    withdrawal_request.processed_at = datetime.utcnow()
                    withdrawal_request.stripe_balance_txn_id = event_object.balance_transaction if hasattr(event_object, 'balance_transaction') else withdrawal_request.stripe_balance_txn_id
                    withdrawal_request.event_id = event_id
                    db.add(withdrawal_request)
                    db.commit()
                
                elif event_type == 'payout.failed':
                    # Payout failed, refund the user via ledger
                    withdrawal_request.status = 'failed'
                    withdrawal_request.event_id = event_id
                    failure_message = event_object.failure_message if hasattr(event_object, 'failure_message') else "Payout failed"
                    
                    # Refund via ledger adjustment (amount + fee)
                    refund_total_minor = withdrawal_request.amount_minor + withdrawal_request.fee_minor
                    try:
                        new_balance = add_ledger_entry(
                            db=db,
                            user_id=withdrawal_request.user_id,
                            currency=withdrawal_request.currency,
                            delta_minor=refund_total_minor,
                            kind='adjustment',
                            external_ref_type='withdrawal',
                            external_ref_id=str(withdrawal_request.id),
                            event_id=event_id,
                            idempotency_key=f"payout_failed_refund_{withdrawal_request.id}",
                            livemode=withdrawal_request.livemode
                        )
                        logger.info(
                            f"Refunded {refund_total_minor} {withdrawal_request.currency} to user "
                            f"{withdrawal_request.user_id} due to failed payout. New balance: {new_balance}"
                        )
                    except ValueError as e:
                        logger.warning(f"Could not process payout refund: {str(e)}")
                    
                    withdrawal_request.admin_notes = f"Payout failed: {failure_message}"
                    db.add(withdrawal_request)
                    db.commit()
                        
                            # TODO: Send email notification about the failed payout
                    logger.warning(f"Payout failed for withdrawal {withdrawal_request.id}. Reason: {failure_message}")
        
        # Refund events
        elif event_type == 'charge.refunded':
            charge_id = event_object.id
            logger.info(f"Processing refund event: {event_type}, Charge ID: {charge_id}")
            background_tasks.add_task(handle_refund, event_object, event.id)
        
        # Dispute events
        elif event_type.startswith('charge.dispute.'):
            dispute_id = event_object.id
            logger.info(f"Processing dispute event: {event_type}, Dispute ID: {dispute_id}")
            background_tasks.add_task(handle_dispute, event_object, event.id)
        
        # Bank account verification events
        elif event_type.startswith('bank_account.'):
            account_id = event_object.id
            logger.info(f"Processing bank account event: {event_type}, ID: {account_id}")
            
            # Find bank account by Stripe ID
            bank_account = db.query(UserBankAccount).filter(
                UserBankAccount.stripe_bank_account_id == account_id
            ).first()
            
            if bank_account:
                if event_type == 'bank_account.verified':
                    bank_account.is_verified = True
                    db.add(bank_account)
                    db.commit()
                
                elif event_type == 'bank_account.verification_failed':
                    bank_account.is_verified = False
                    db.add(bank_account)
                    db.commit()
        
        # Mark event as processed
        if event_id:
            webhook_event = db.query(StripeWebhookEvent).filter(
                StripeWebhookEvent.event_id == event_id
            ).first()
            if webhook_event:
                webhook_event.status = 'processed'
                webhook_event.processed_at = datetime.utcnow()
                db.add(webhook_event)
                db.commit()
        
        return {"status": "success"}
    
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        # Mark event as failed
        if event_id:
            webhook_event = db.query(StripeWebhookEvent).filter(
                StripeWebhookEvent.event_id == event_id
            ).first()
            if webhook_event:
                webhook_event.status = 'failed'
                webhook_event.last_error = str(e)
                webhook_event.processed_at = datetime.utcnow()
                db.add(webhook_event)
                db.commit()
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint to get user's wallet balance
@router.get("/wallet-balance")
async def get_wallet_balance(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    ## Get User's Wallet Balance
    
    Retrieves the user's current wallet balance and recent transactions.
    
    ### Use this endpoint to:
    - Display the user's current wallet balance
    - Show recent successful transactions in their account
    - Track when the wallet was last updated
    
    ### Returns:
    - `wallet_balance`: Current balance in the user's wallet (in dollars)
    - `currency`: Currency of the balance (default: 'USD')
    - `last_updated`: Timestamp of when the wallet was last updated
    - `recent_transactions`: List of up to 5 most recent successful transactions
    
    ### Transaction fields:
    - `id`: Transaction ID
    - `amount`: Amount in dollars
    - `currency`: Currency code
    - `created_at`: When the transaction was created
    - `payment_method_type`: Type of payment method used
    - `transaction_type`: Type of transaction (e.g., 'wallet_deposit', 'wallet_withdrawal')
    """
    try:
        # Get user from token
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get balance from wallet ledger (new system)
        balance_minor = get_balance(db, user.account_id, 'usd')
        balance_dollars = balance_minor / 100.0
        
        # Get recent transactions
        recent_transactions = db.query(PaymentTransaction).filter(
            PaymentTransaction.user_id == user.account_id,
            PaymentTransaction.status == "succeeded"
        ).order_by(PaymentTransaction.created_at.desc()).limit(5).all()
        
        # Format transactions for response
        transactions = []
        for tx in recent_transactions:
            # Use amount_minor if available, otherwise convert amount
            amount_minor = tx.amount_minor if tx.amount_minor is not None else int(tx.amount * 100)
            transactions.append({
                "id": tx.id,
                "amount": tx.amount,  # Keep for backward compatibility
                "amount_minor": amount_minor,
                "currency": tx.currency,
                "created_at": tx.created_at.isoformat() if tx.created_at else None,
                "payment_method_type": tx.payment_method_type,
                "transaction_type": json.loads(tx.payment_metadata).get("transaction_type") if tx.payment_metadata else None
            })
        
        return {
            "wallet_balance": balance_dollars,  # Keep for backward compatibility
            "wallet_balance_minor": balance_minor,
            "currency": user.wallet_currency or "usd",
            "last_updated": user.last_wallet_update.isoformat() if user.last_wallet_update else None,
            "recent_transactions": transactions
        }
    except Exception as e:
        logger.error(f"Error retrieving wallet balance: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint to withdraw funds from wallet
@router.post("/withdraw-from-wallet")
async def withdraw_from_wallet(
    amount_minor: int = Body(..., description="Amount in minor units (cents) to withdraw from wallet (e.g., 2500 for $25.00)"),
    currency: str = Body("usd", description="Three-letter currency code (e.g., 'usd', 'eur')"),
    method: str = Body(..., description="Method for withdrawal ('standard' or 'instant')"),
    bank_account_id: Optional[int] = Body(None, description="ID of the saved bank account to use for withdrawal"),
    payout_details: Optional[Dict[str, Any]] = Body(None, description="Details needed for the payout if not using a saved bank account"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    ## Withdraw Funds from Wallet
    
    Creates a withdrawal request to transfer funds from the user's wallet to their bank account.
    
    ### Use this endpoint to:
    - Allow users to cash out their wallet balance
    - Process withdrawal requests using standard or instant methods
    
    ### Request Body:
    - `amount_minor`: Amount in minor units (cents) to withdraw (e.g., 2500 for $25.00)
    - `currency`: Three-letter currency code (default: 'usd')
    - `method`: Method for withdrawal ('standard' or 'instant')
      - 'standard': Free, processed within 1-3 business days, subject to admin review
      - 'instant': Instant transfer, 1.5% fee (minimum $0.50), no admin review required
    - `bank_account_id`: (Optional) ID of a saved bank account to use
    - `payout_details`: (Optional) Details needed if not using a saved bank account
    
    ### Returns:
    - `status`: Status of the withdrawal request
    - `amount_minor`: Amount being withdrawn in minor units
    - `fee_minor`: Processing fee in minor units (for instant transfers)
    - `net_amount_minor`: Final amount after fees in minor units
    - `currency`: Currency of the withdrawal
    - `withdrawal_request_id`: ID of the created withdrawal request
    - `balance_after_minor`: New wallet balance after withdrawal
    - `message`: Informational message about the withdrawal process
    
    ### Note:
    Standard withdrawals require admin approval before funds are sent.
    Instant withdrawals are processed immediately with a fee.
    """
    try:
        # Get user from the database
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Validate inputs
        if amount_minor <= 0:
            raise HTTPException(status_code=400, detail="Amount must be greater than 0")
        
        if method not in ["standard", "instant"]:
            raise HTTPException(status_code=400, detail="Invalid method. Must be 'standard' or 'instant'")
        
        if not validate_currency(currency):
            raise HTTPException(status_code=400, detail=f"Invalid currency code: {currency}")
        
        # Calculate processing fee for instant transfers (1.5% with minimum $0.50 = 50 cents)
        fee_minor = 0
        if method == "instant":
            fee_minor = max(math.ceil(Decimal(amount_minor) * Decimal("0.015")), 50)
            
        # Calculate total amount needed (withdrawal + fee)
        total_amount_minor = amount_minor + fee_minor
        
        # Get or create balance row, then lock it to prevent concurrent withdrawals
        # This ensures atomic balance checking and withdrawal creation
        balance_row = db.query(UserWalletBalance).filter(
            UserWalletBalance.user_id == user.account_id,
            UserWalletBalance.currency == currency
        ).first()
        
        if not balance_row:
            # Create balance row if it doesn't exist (start at 0)
            balance_row = UserWalletBalance(
                user_id=user.account_id,
                currency=currency,
                balance_minor=0,
                last_recalculated_at=datetime.utcnow()
            )
            db.add(balance_row)
            db.flush()  # Flush to get the row in the database
        
        # Now lock the row (whether it existed or was just created)
        locked_balance_row = db.query(UserWalletBalance).filter(
            UserWalletBalance.user_id == user.account_id,
            UserWalletBalance.currency == currency
        ).with_for_update(nowait=True).first()
        
        if not locked_balance_row:
            # This shouldn't happen, but handle it gracefully
            raise HTTPException(status_code=500, detail="Failed to lock wallet balance")
        
        current_balance_minor = locked_balance_row.balance_minor
        
        # Check for pending withdrawals that would overdraw (while holding lock)
        pending_withdrawals = db.query(func.sum(WithdrawalRequest.amount_minor + WithdrawalRequest.fee_minor)).filter(
            WithdrawalRequest.user_id == user.account_id,
            WithdrawalRequest.currency == currency,
            WithdrawalRequest.status.in_(['pending', 'processing', 'approved'])
        ).scalar() or 0
        
        available_balance = current_balance_minor - pending_withdrawals
        
        # Validate sufficient balance
        if available_balance < total_amount_minor:
            raise HTTPException(
                status_code=400, 
                detail=f"Insufficient funds. Available balance: {available_balance / 100.0:.2f} {currency.upper()}, "
                       f"Required: {amount_minor / 100.0:.2f} + {fee_minor / 100.0:.2f} fee = {total_amount_minor / 100.0:.2f} {currency.upper()}"
            )
        
        # Validate bank account
        bank_account = None
        if bank_account_id:
            bank_account = db.query(UserBankAccount).filter(
                UserBankAccount.id == bank_account_id,
                UserBankAccount.user_id == user.account_id
            ).first()
            
            if not bank_account:
                raise HTTPException(status_code=404, detail="Bank account not found")
            
            if not bank_account.is_verified:
                raise HTTPException(status_code=400, detail="Bank account is not verified")
        elif not payout_details:
            raise HTTPException(
                status_code=400, 
                detail="Either bank_account_id or payout_details must be provided"
            )
        
        # Create withdrawal request
        withdrawal_request = WithdrawalRequest(
            user_id=user.account_id,
            amount_minor=amount_minor,
            currency=currency,
            method=method,
            fee_minor=fee_minor,
            status='pending' if method == 'standard' else 'processing',
            requested_at=datetime.utcnow(),
            livemode=False  # TODO: Get from config
        )
        
        db.add(withdrawal_request)
        db.flush()  # Get the ID
        
        # Insert ledger entries atomically
        # Note: add_ledger_entry will lock again, but since we're in the same transaction,
        # PostgreSQL will allow it (same transaction can re-acquire the same lock)
        new_balance = current_balance_minor
        
        # Fee entry (if instant)
        if fee_minor > 0:
            try:
                new_balance = add_ledger_entry(
                    db=db,
                    user_id=user.account_id,
                    currency=currency,
                    delta_minor=-fee_minor,
                    kind='fee',
                    external_ref_type='withdrawal',
                    external_ref_id=str(withdrawal_request.id),
                    idempotency_key=f"withdrawal_fee_{withdrawal_request.id}",
                    livemode=False
                )
            except ValueError as e:
                # Idempotency - already processed
                logger.warning(f"Fee ledger entry already exists: {str(e)}")
                # Get current balance
                new_balance = get_balance(db, user.account_id, currency)
        
        # Withdrawal entry (principal)
        try:
            new_balance = add_ledger_entry(
                db=db,
                user_id=user.account_id,
                currency=currency,
                delta_minor=-amount_minor,
                kind='withdraw',
                external_ref_type='withdrawal',
                external_ref_id=str(withdrawal_request.id),
                idempotency_key=f"withdrawal_{withdrawal_request.id}",
                livemode=False
            )
        except ValueError as e:
            # Idempotency - already processed
            logger.warning(f"Withdrawal ledger entry already exists: {str(e)}")
            # Get current balance
            new_balance = get_balance(db, user.account_id, currency)
        
        db.commit()
        db.refresh(withdrawal_request)
        
        logger.info(
            f"{method.capitalize()} withdrawal of {amount_minor} {currency} "
            f"(fee: {fee_minor}) requested by user {user.account_id}. "
            f"New balance: {new_balance}"
        )
        
        # For instant transfers, attempt to process immediately
        if method == "instant":
            try:
                # TODO: When Stripe Connect is implemented, create transfer/payout here
                # For now, mark as processing (admin will complete via process_withdrawal)
                withdrawal_request.status = 'processing'
                db.add(withdrawal_request)
                db.commit()
                
                return {
                    "status": "processing",
                    "amount_minor": amount_minor,
                    "fee_minor": fee_minor,
                    "net_amount_minor": amount_minor,  # User receives amount, fee is separate
                    "currency": currency,
                    "withdrawal_request_id": withdrawal_request.id,
                    "balance_after_minor": new_balance,
                    "message": "Instant withdrawal initiated. Funds will be transferred shortly."
                }
            except Exception as e:
                logger.error(f"Error processing instant withdrawal: {str(e)}", exc_info=True)
                # Revert ledger entries via adjustment
                try:
                    add_ledger_entry(
                        db=db,
                        user_id=user.account_id,
                        currency=currency,
                        delta_minor=total_amount_minor,  # Refund total
                        kind='adjustment',
                        external_ref_type='withdrawal',
                        external_ref_id=str(withdrawal_request.id),
                        idempotency_key=f"withdrawal_reversal_{withdrawal_request.id}",
                        livemode=False
                    )
                    withdrawal_request.status = 'failed'
                    db.add(withdrawal_request)
                    db.commit()
                except Exception as revert_error:
                    logger.error(f"Failed to revert withdrawal: {str(revert_error)}", exc_info=True)
                    db.rollback()
                
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to process instant withdrawal: {str(e)}"
                )
        
        # For standard transfers, return pending status
        return {
            "status": "pending",
            "amount_minor": amount_minor,
            "fee_minor": fee_minor,
            "net_amount_minor": amount_minor,
            "currency": currency,
            "withdrawal_request_id": withdrawal_request.id,
            "balance_after_minor": new_balance,
            "message": "Withdrawal request submitted successfully. Funds will be transferred within 1-3 business days after admin approval."
        }
            
    except HTTPException:
        db.rollback()
        raise
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error processing withdrawal: {str(e)}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint to get withdrawal history
@router.get("/withdrawal-history")
async def get_withdrawal_history(
    currency: Optional[str] = Query(None, description="Filter by currency code (e.g., 'usd')"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = 10,
    offset: int = 0
):
    """
    ## Get User's Withdrawal History
    
    Retrieves the history of wallet withdrawal requests made by the user.
    
    ### Use this endpoint to:
    - Display a list of past withdrawal requests
    - Check the status of pending withdrawals
    - View details of completed withdrawals
    
    ### Query Parameters:
    - `currency`: (Optional) Filter by currency code
    - `limit`: Maximum number of records to return (default: 10)
    - `offset`: Number of records to skip for pagination (default: 0)
    
    ### Returns:
    - `withdrawals`: List of withdrawal requests with the following fields:
        - `id`: Withdrawal request ID
        - `amount_minor`: Amount in minor units
        - `fee_minor`: Fee in minor units
        - `currency`: Currency code
        - `status`: Status of the withdrawal ('pending', 'processing', 'paid', 'failed', 'canceled')
        - `method`: Method used for withdrawal ('standard' or 'instant')
        - `requested_at`: When the withdrawal was requested
        - `processed_at`: When the withdrawal was processed (if applicable)
    - `total_count`: Total number of withdrawal records for pagination
    """
    try:
        # Get user from token
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Build query
        query = db.query(WithdrawalRequest).filter(
            WithdrawalRequest.user_id == user.account_id
        )
        
        # Filter by currency if provided
        if currency:
            if not validate_currency(currency):
                raise HTTPException(status_code=400, detail=f"Invalid currency code: {currency}")
            query = query.filter(WithdrawalRequest.currency == currency)
        
        # Get total count before pagination
        total_count = query.count()
        
        # Get paginated results
        withdrawals = query.order_by(WithdrawalRequest.requested_at.desc()).offset(offset).limit(limit).all()
        
        # Format the results
        withdrawal_history = []
        for withdrawal in withdrawals:
                withdrawal_history.append({
                    "id": withdrawal.id,
                "amount_minor": withdrawal.amount_minor,
                "fee_minor": withdrawal.fee_minor,
                    "currency": withdrawal.currency,
                    "status": withdrawal.status,
                "method": withdrawal.method,
                "requested_at": withdrawal.requested_at.isoformat() if withdrawal.requested_at else None,
                "processed_at": withdrawal.processed_at.isoformat() if withdrawal.processed_at else None,
                "admin_notes": withdrawal.admin_notes
            })
        
        return {
            "withdrawals": withdrawal_history,
            "total_count": total_count
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving withdrawal history: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# Admin endpoint to process withdrawals
@router.post("/admin/process-withdrawal/{withdrawal_request_id}")
async def process_withdrawal(
    withdrawal_request_id: int,
    status: str = Body(..., description="New status for the withdrawal ('paid' or 'failed')"),
    notes: Optional[str] = Body(None, description="Admin notes about the withdrawal process or reason for failure"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    ## Process Withdrawal Request (Admin Only)
    
    Allows administrators to approve or reject withdrawal requests.
    
    ### Use this endpoint to:
    - Mark standard withdrawals as paid after sending funds via Stripe
    - Reject withdrawals that cannot be processed
    - Add notes about the processing outcome
    - Review details of instant withdrawals (but cannot change their status if already processed)
    
    ### Path Parameters:
    - `withdrawal_request_id`: ID of the withdrawal request to process
    
    ### Request Body:
    - `status`: New status for the withdrawal:
        - 'paid': Mark the withdrawal as successfully processed (funds sent)
        - 'failed': Mark the withdrawal as failed, which will refund the amount to the user's wallet
    - `notes`: Optional admin notes about the withdrawal process or reason for failure
    
    ### Returns:
    - `withdrawal_request_id`: ID of the processed withdrawal request
    - `status`: Updated status
    - `notes`: Admin notes provided
    - `method`: Type of withdrawal ('standard' or 'instant')
    - `processed_at`: Timestamp of the processing
    - `message`: Additional information about the action taken
    
    ### Note:
    This endpoint requires admin privileges. If a withdrawal is marked as failed,
    the amount (including fees) will be automatically refunded to the user's wallet via ledger.
    
    Instant withdrawals that have already been processed cannot have their status changed,
    but admins can still review them and add notes.
    """
    try:
        # Check if user is admin
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        admin_user = db.query(User).filter(User.sub == sub).first()
        if not admin_user or not admin_user.is_admin:
            raise HTTPException(status_code=403, detail="Admin access required")
        
        # Get the withdrawal request
        withdrawal_request = db.query(WithdrawalRequest).filter(
            WithdrawalRequest.id == withdrawal_request_id
        ).first()
        
        if not withdrawal_request:
            raise HTTPException(status_code=404, detail=f"Withdrawal request {withdrawal_request_id} not found")
        
        # For instant withdrawals that are already processed, don't allow status changes
        if withdrawal_request.method == "instant" and withdrawal_request.status in ["paid", "failed"]:
            # Just add admin notes without changing status
            if notes:
                withdrawal_request.admin_notes = notes
                db.add(withdrawal_request)
                db.commit()
            
            return {
                "withdrawal_request_id": withdrawal_request_id,
                "status": withdrawal_request.status,
                "notes": notes,
                "method": withdrawal_request.method,
                "processed_at": withdrawal_request.processed_at.isoformat() if withdrawal_request.processed_at else None,
                "message": "This instant withdrawal has already been processed and cannot be changed. Notes added for record keeping."
            }
        
        # Validate status
        if status not in ["paid", "failed"]:
            raise HTTPException(status_code=400, detail="Status must be 'paid' or 'failed'")
        
        old_status = withdrawal_request.status
        
        # If the withdrawal failed, refund the amount to the user's wallet via ledger
        if status == "failed" and old_status != "failed":
            # Calculate total refund (amount + fee)
            refund_total_minor = withdrawal_request.amount_minor + withdrawal_request.fee_minor
            
            # Refund via ledger adjustment
            try:
                new_balance = add_ledger_entry(
                    db=db,
                    user_id=withdrawal_request.user_id,
                    currency=withdrawal_request.currency,
                    delta_minor=refund_total_minor,
                    kind='adjustment',
                    external_ref_type='withdrawal',
                    external_ref_id=str(withdrawal_request.id),
                    idempotency_key=f"withdrawal_refund_{withdrawal_request.id}",
                    livemode=withdrawal_request.livemode
                )
                logger.info(
                    f"Refunded {refund_total_minor} {withdrawal_request.currency} to user "
                    f"{withdrawal_request.user_id} due to failed withdrawal. New balance: {new_balance}"
                )
            except ValueError as e:
                logger.warning(f"Could not process refund: {str(e)}")
        
        # For paid standard withdrawals, create Stripe payout (when Connect is ready)
        if status == "paid" and withdrawal_request.method == "standard" and old_status != "paid":
            try:
                # TODO: When Stripe Connect is implemented:
                # 1. Get connected account for user
                # 2. Create Transfer to connected account
                # 3. Create Payout from connected account (or use auto-payout)
                # 4. Store stripe_transfer_id and stripe_payout_id
                
                # For now, simulate a successful payout
                payout_id = f"po_std_{int(datetime.utcnow().timestamp())}"
                withdrawal_request.stripe_payout_id = payout_id
                withdrawal_request.stripe_balance_txn_id = f"txn_{int(datetime.utcnow().timestamp())}"
                
                logger.info(
                    f"Standard withdrawal {withdrawal_request_id} processed: "
                    f"{withdrawal_request.amount_minor} {withdrawal_request.currency} for user {withdrawal_request.user_id}"
                )
            except Exception as e:
                logger.error(f"Error creating Stripe payout: {str(e)}", exc_info=True)
                # Don't fail the request, just log the error
        
        # Update withdrawal request
        withdrawal_request.status = status
        withdrawal_request.admin_id = admin_user.account_id
        withdrawal_request.admin_notes = notes
        withdrawal_request.processed_at = datetime.utcnow()
        
        db.add(withdrawal_request)
        db.commit()
        db.refresh(withdrawal_request)
        
        logger.info(
            f"Withdrawal {withdrawal_request_id} marked as {status} by admin {admin_user.account_id}"
        )
        
        return {
            "withdrawal_request_id": withdrawal_request_id,
            "status": status,
            "notes": notes,
            "method": withdrawal_request.method,
            "processed_at": withdrawal_request.processed_at.isoformat() if withdrawal_request.processed_at else None,
            "message": f"{withdrawal_request.method.capitalize()} withdrawal successfully {status}."
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error processing withdrawal status update: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# Bank Account Management Endpoints
#---------------------------------

@router.post("/bank-accounts", response_model=BankAccountResponse)
async def add_bank_account(
    bank_account: BankAccountRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    ## Add Bank Account
    
    Adds a new bank account for the user that can be used for withdrawals.
    
    ### Use this endpoint to:
    - Allow users to save bank account details for future withdrawals
    - Set up bank accounts for automated payments
    
    ### Request Body:
    - `account_holder_name`: Full name of the account holder
    - `account_number`: Bank account number (will be encrypted)
    - `routing_number`: Bank routing number (will be encrypted)
    - `bank_name`: Name of the bank
    - `is_default`: Whether this account should be the default (optional, default: false)
    
    ### Returns:
    - Bank account object with the following fields:
        - `id`: ID of the saved bank account
        - `account_name`: Name on the account
        - `account_number_last4`: Last 4 digits of the account number
        - `bank_name`: Name of the bank
        - `is_default`: Whether this is the default account
        - `is_verified`: Whether the account has been verified
        - `created_at`: When the account was added
    
    ### Note:
    Bank account details are encrypted for security. Only the last 4 digits
    of the account number are stored in plain text.
    """
    try:
        # Get user from token
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Encrypt sensitive data
        encrypted_account_number = encrypt_data(bank_account.account_number)
        encrypted_routing_number = encrypt_data(bank_account.routing_number)
        account_number_last4 = get_last_four(bank_account.account_number)
        
        # If this is set as default, unset any existing default
        if bank_account.is_default:
            existing_default = db.query(UserBankAccount).filter(
                UserBankAccount.user_id == user.account_id,
                UserBankAccount.is_default == True
            ).first()
            
            if existing_default:
                existing_default.is_default = False
                db.add(existing_default)
        
        # Create bank account record
        new_account = UserBankAccount(
            user_id=user.account_id,
            account_name=bank_account.account_holder_name,
            account_number_last4=account_number_last4,
            account_number_encrypted=encrypted_account_number,
            routing_number_encrypted=encrypted_routing_number,
            bank_name=bank_account.bank_name,
            is_default=bank_account.is_default,
            is_verified=False  # Initial state, will be verified later
        )
        
        db.add(new_account)
        db.commit()
        db.refresh(new_account)
        
        # Start bank account verification with Stripe (in production)
        # For this implementation, we'll skip actual Stripe integration
        # and just mark it as verified for demonstration
        try:
            # In production, this would use Stripe API to set up verification
            # For test/dev, we'll simulate verification
            new_account.is_verified = True
            new_account.stripe_bank_account_id = f"ba_{int(datetime.utcnow().timestamp())}"
            db.add(new_account)
            db.commit()
            db.refresh(new_account)
        except Exception as e:
            logger.error(f"Error during bank verification: {e}")
            # Continue anyway - we'll mark as unverified
        
        return new_account
    except Exception as e:
        logger.error(f"Error adding bank account: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/bank-accounts", response_model=List[BankAccountResponse])
async def list_bank_accounts(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    ## List User's Bank Accounts
    
    Retrieves all saved bank accounts for the user.
    
    ### Use this endpoint to:
    - Display a list of the user's saved bank accounts
    - Allow selection of accounts for withdrawals
    
    ### Returns:
    - List of bank account objects, each containing:
        - `id`: ID of the saved bank account
        - `account_name`: Name on the account
        - `account_number_last4`: Last 4 digits of the account number
        - `bank_name`: Name of the bank
        - `is_default`: Whether this is the default account
        - `is_verified`: Whether the account has been verified
        - `created_at`: When the account was added
    
    ### Note:
    Results are ordered with the default account first, then by creation date (newest first).
    """
    try:
        # Get user from token
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get bank accounts
        accounts = db.query(UserBankAccount).filter(
            UserBankAccount.user_id == user.account_id
        ).order_by(UserBankAccount.is_default.desc(), UserBankAccount.created_at.desc()).all()
        
        return accounts
    except Exception as e:
        logger.error(f"Error getting bank accounts: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/bank-accounts/{account_id}", response_model=dict)
async def delete_bank_account(
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    ## Delete Bank Account
    
    Removes a saved bank account from the user's profile.
    
    ### Use this endpoint to:
    - Allow users to delete bank accounts they no longer want to use
    - Remove outdated or incorrect bank information
    
    ### Path Parameters:
    - `account_id`: ID of the bank account to delete
    
    ### Returns:
    - `success`: Boolean indicating whether the deletion was successful
    - `message`: Confirmation message
    
    ### Note:
    This will permanently remove the bank account information.
    If the account is the default, another account will be set as default if available.
    """
    try:
        # Get user from token
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get bank account
        account = db.query(UserBankAccount).filter(
            UserBankAccount.id == account_id,
            UserBankAccount.user_id == user.account_id
        ).first()
        
        if not account:
            raise HTTPException(status_code=404, detail="Bank account not found")
        
        # Delete the account
        db.delete(account)
        db.commit()
        
        return {"message": "Bank account deleted successfully"}
    except Exception as e:
        logger.error(f"Error deleting bank account: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# Subscription Management Endpoints
#---------------------------------

class SubscriptionRequest(BaseModel):
    """
    Request model for creating a new subscription.
    """
    plan_id: int = Field(..., description="ID of the subscription plan to subscribe to (get available plans from /subscriptions/plans endpoint)")
    payment_method_id: Optional[str] = Field(None, description="ID of the Stripe payment method to use for billing. If not provided, user will need to provide payment details.")
    auto_renew: bool = Field(True, description="Whether the subscription should automatically renew at the end of the billing period")

class SubscriptionResponse(BaseModel):
    """
    Response model for subscription information.
    """
    id: int = Field(..., description="Unique identifier for the subscription")
    plan_name: str = Field(..., description="Name of the subscription plan")
    price: float = Field(..., description="Price in USD per billing period")
    billing_interval: str = Field(..., description="Frequency of billing ('month' or 'year')")
    status: str = Field(..., description="Current status ('active', 'canceled', 'past_due', etc.)")
    current_period_end: Optional[datetime] = Field(None, description="When the current billing period ends")
    cancel_at_period_end: bool = Field(..., description="Whether the subscription will cancel at the end of the current period")
    
    class Config:
        from_attributes = True

@router.get("/subscriptions/plans", response_model=List[dict])
async def list_subscription_plans(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    ## List Available Subscription Plans
    
    Retrieves all available subscription plans that users can subscribe to.
    
    ### Use this endpoint to:
    - Display subscription options to users
    - Show pricing information before subscription
    - Get plan IDs needed for creating subscriptions
    
    ### Returns:
    - List of subscription plan objects, each containing:
        - `id`: Plan ID (needed for subscription creation)
        - `name`: Name of the plan
        - `description`: Detailed description of the plan
        - `price_usd`: Price in USD per billing period
        - `billing_interval`: Billing frequency ('month' or 'year')
        - `features`: List of features included in the plan (if available)
    
    ### Note:
    This endpoint is typically called before creating a subscription to show
    available options to the user.
    """
    try:
        # Get user from token to ensure authentication
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        # Get all active subscription plans
        plans = db.query(SubscriptionPlan).order_by(
            SubscriptionPlan.price_usd.asc()
        ).all()
        
        # Format the results
        result = []
        for plan in plans:
            plan_data = {
                "id": plan.id,
                "name": plan.name,
                "description": plan.description,
                "price_usd": plan.price_usd,
                "billing_interval": plan.billing_interval,
                "features": json.loads(plan.features) if plan.features else []
            }
            result.append(plan_data)
        
        return result
    
    except Exception as e:
        logger.error(f"Error getting subscription plans: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/subscriptions", response_model=dict)
async def create_subscription(
    subscription: SubscriptionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    ## Create Subscription
    
    Creates a new subscription for premium features or services.
    
    ### Use this endpoint to:
    - Sign up users for recurring subscription plans
    - Process initial subscription payments
    
    ### Request Body:
    - `plan_id`: ID of the subscription plan to subscribe to
    - `payment_method_id`: ID of the Stripe payment method to use for billing. If not provided, user will need to provide payment details.
    - `auto_renew`: Whether the subscription should automatically renew at the end of the billing period
    
    ### Returns:
    - `client_secret`: Stripe PaymentIntent client secret for the initial payment
    - `subscription_id`: ID of the created subscription
    - `status`: Initial status of the subscription (typically 'incomplete')
    - `message`: Informational message about next steps
    
    ### Note:
    The subscription will not be active until the initial payment is confirmed.
    Users can only have one active subscription at a time.
    """
    try:
        # Get user from token
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get subscription plan
        plan = db.query(SubscriptionPlan).filter(
            SubscriptionPlan.id == subscription.plan_id
        ).first()
        
        if not plan:
            raise HTTPException(status_code=404, detail=f"Subscription plan with ID {subscription.plan_id} not found")
        
        # Check if user already has an active subscription
        existing_sub = db.query(UserSubscription).filter(
            UserSubscription.user_id == user.account_id,
            UserSubscription.status == "active"
        ).first()
        
        if existing_sub:
            raise HTTPException(
                status_code=400, 
                detail="User already has an active subscription. Please cancel it before subscribing to a new plan."
            )
        
        # In production, create a Stripe subscription
        # Here we'll simulate it
        now = datetime.utcnow()
        period_end = now + timedelta(days=30 if plan.billing_interval == 'month' else 365)
        
        # Create a payment intent for the initial payment
        amount_in_cents = int(plan.price_usd * 100)
        
        try:
            # Create a real payment intent in Stripe
            intent = stripe.PaymentIntent.create(
                amount=amount_in_cents,
                currency="usd",
                payment_method=subscription.payment_method_id,
                metadata={
                    "user_id": str(user.account_id),
                    "sub": sub,
                    "plan_id": str(plan.id),
                    "transaction_type": "subscription_payment"
                },
                automatic_payment_methods={"enabled": True}
            )
            
            # Create a subscription record
            new_subscription = UserSubscription(
                user_id=user.account_id,
                plan_id=plan.id,
                stripe_subscription_id=f"sub_{int(datetime.utcnow().timestamp())}",
                status="incomplete",  # Will become active once payment is confirmed
                current_period_start=now,
                current_period_end=period_end,
                cancel_at_period_end=not subscription.auto_renew,
                payment_method_id=subscription.payment_method_id
            )
            
            db.add(new_subscription)
            db.commit()
            db.refresh(new_subscription)
            
            return {
                "client_secret": intent.client_secret,
                "subscription_id": new_subscription.id,
                "status": "incomplete",
                "message": "Please complete the payment to activate your subscription."
            }
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating subscription: {e}")
            raise HTTPException(status_code=400, detail=str(e))
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating subscription: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/subscriptions", response_model=List[SubscriptionResponse])
async def list_subscriptions(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    ## List User's Subscriptions
    
    Retrieves all subscriptions for the current user, both active and inactive.
    
    ### Use this endpoint to:
    - Display a user's subscription history
    - Check active subscription status
    - View subscription renewal dates
    
    ### Returns:
    - List of subscription objects, each containing:
        - `id`: Subscription ID
        - `plan_name`: Name of the subscription plan
        - `price`: Price in USD
        - `billing_interval`: Billing frequency ('month' or 'year')
        - `status`: Current status of the subscription
        - `current_period_end`: Date when the current billing period ends
        - `cancel_at_period_end`: Whether the subscription will cancel at the period end
    
    ### Note:
    Results are ordered by creation date (newest first).
    """
    try:
        # Get user from token
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get subscriptions with plan info
        subscriptions = db.query(
            UserSubscription,
            SubscriptionPlan.name,
            SubscriptionPlan.price_usd,
            SubscriptionPlan.billing_interval
        ).join(
            SubscriptionPlan, UserSubscription.plan_id == SubscriptionPlan.id
        ).filter(
            UserSubscription.user_id == user.account_id
        ).order_by(UserSubscription.created_at.desc()).all()
        
        # Format results
        result = []
        for sub, plan_name, price, interval in subscriptions:
            result.append({
                "id": sub.id,
                "plan_name": plan_name,
                "price": price,
                "billing_interval": interval,
                "status": sub.status,
                "current_period_end": sub.current_period_end,
                "cancel_at_period_end": sub.cancel_at_period_end
            })
        
        return result
    except Exception as e:
        logger.error(f"Error getting subscriptions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/subscriptions/{subscription_id}/cancel", response_model=dict)
async def cancel_subscription(
    subscription_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    ## Cancel Subscription
    
    Cancels a subscription at the end of the current billing period.
    
    ### Use this endpoint to:
    - Allow users to cancel their subscriptions
    - Prevent subscription renewal at the end of the billing period
    
    ### Path Parameters:
    - `subscription_id`: ID of the subscription to cancel
    
    ### Returns:
    - `message`: Confirmation message
    - `current_period_end`: Date when the subscription will end
    
    ### Note:
    This sets the subscription to not renew at the end of the current period.
    The subscription will remain active until the end of the current billing period.
    """
    try:
        # Get user from token
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get subscription
        subscription = db.query(UserSubscription).filter(
            UserSubscription.id == subscription_id,
            UserSubscription.user_id == user.account_id
        ).first()
        
        if not subscription:
            raise HTTPException(status_code=404, detail="Subscription not found")
        
        if subscription.status != "active":
            raise HTTPException(status_code=400, detail="Only active subscriptions can be canceled")
        
        # Update subscription
        subscription.cancel_at_period_end = True
        db.add(subscription)
        db.commit()
        
        return {
            "message": "Subscription will be canceled at the end of the billing period",
            "current_period_end": subscription.current_period_end
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error canceling subscription: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# Admin endpoint to view all withdrawal transactions
@router.get("/admin/withdrawals")
async def admin_get_withdrawals(
    method: Optional[str] = Query(None, description="Filter by withdrawal method: 'standard' or 'instant'"),
    status: Optional[str] = Query(None, description="Filter by status: 'pending', 'processing', 'paid', 'failed', 'canceled'"),
    user_id: Optional[int] = Query(None, description="Filter by user ID"),
    currency: Optional[str] = Query(None, description="Filter by currency code"),
    limit: int = Query(20, description="Maximum number of records to return"),
    offset: int = Query(0, description="Number of records to skip"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    ## Get All Withdrawal Requests (Admin Only)
    
    Returns a paginated list of withdrawal requests with filtering options.
    
    ### Use this endpoint to:
    - View and manage all withdrawal requests
    - Filter requests by method, status, user, or currency
    - Monitor instant vs standard withdrawals
    
    ### Query Parameters:
    - `method`: (Optional) Filter by 'standard' or 'instant'
    - `status`: (Optional) Filter by status ('pending', 'processing', 'paid', 'failed', 'canceled')
    - `user_id`: (Optional) Filter by specific user ID
    - `currency`: (Optional) Filter by currency code
    - `limit`: Maximum number of records to return (default: 20)
    - `offset`: Number of records to skip for pagination (default: 0)
    
    ### Returns:
    - `withdrawals`: List of withdrawal requests with details
    - `total_count`: Total number of withdrawals matching the filters
    - `summary`: Summary statistics by type and status
    
    ### Note:
    This endpoint requires admin privileges.
    """
    try:
        # Check if user is admin
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        admin_user = db.query(User).filter(User.sub == sub).first()
        if not admin_user or not admin_user.is_admin:
            raise HTTPException(status_code=403, detail="Admin access required")
        
        # Build the base query for withdrawal requests
        base_query = db.query(WithdrawalRequest)
        
        # Apply method filter if provided
        if method:
            if method not in ["standard", "instant"]:
                raise HTTPException(status_code=400, detail="Method must be 'standard' or 'instant'")
            base_query = base_query.filter(WithdrawalRequest.method == method)
        
        # Apply status filter if provided
        if status:
            if status not in ["pending", "processing", "paid", "failed", "canceled"]:
                raise HTTPException(
                    status_code=400, 
                    detail="Status must be one of: 'pending', 'processing', 'paid', 'failed', 'canceled'"
                )
            base_query = base_query.filter(WithdrawalRequest.status == status)
        
        # Apply user filter if provided
        if user_id:
            base_query = base_query.filter(WithdrawalRequest.user_id == user_id)
        
        # Apply currency filter if provided
        if currency:
            if not validate_currency(currency):
                raise HTTPException(status_code=400, detail=f"Invalid currency code: {currency}")
            base_query = base_query.filter(WithdrawalRequest.currency == currency)
        
        # Get total counts for summary stats
        total_count = base_query.count()
        
        # Get counts by method
        instant_count = db.query(WithdrawalRequest).filter(
            WithdrawalRequest.method == "instant"
        ).count()
        
        standard_count = db.query(WithdrawalRequest).filter(
            WithdrawalRequest.method == "standard"
        ).count()
        
        # Get counts by status
        pending_count = db.query(WithdrawalRequest).filter(
            WithdrawalRequest.status == "pending"
        ).count()
        
        processing_count = db.query(WithdrawalRequest).filter(
            WithdrawalRequest.status == "processing"
        ).count()
        
        paid_count = db.query(WithdrawalRequest).filter(
            WithdrawalRequest.status == "paid"
        ).count()
        
        failed_count = db.query(WithdrawalRequest).filter(
            WithdrawalRequest.status == "failed"
        ).count()
        
        # Get paginated results
        withdrawals = base_query.order_by(
            WithdrawalRequest.requested_at.desc()
        ).offset(offset).limit(limit).all()
        
        # Format each withdrawal with user details
        formatted_withdrawals = []
        for withdrawal in withdrawals:
            try:
                # Get user details
                withdrawal_user = db.query(User).filter(User.account_id == withdrawal.user_id).first()
                user_email = withdrawal_user.email if withdrawal_user else "Unknown"
                user_name = f"{withdrawal_user.first_name or ''} {withdrawal_user.last_name or ''}".strip() or withdrawal_user.username if withdrawal_user else "Unknown"
                
                # Get bank account details if available
                bank_account = db.query(UserBankAccount).filter(
                    UserBankAccount.user_id == withdrawal.user_id,
                    UserBankAccount.is_default == True
                ).first()
                
                bank_details = {
                    "account_name": bank_account.account_name if bank_account else "N/A",
                    "bank_name": bank_account.bank_name if bank_account else "N/A",
                    "account_last4": bank_account.account_number_last4 if bank_account else "N/A"
                }
                
                formatted_withdrawals.append({
                    "id": withdrawal.id,
                    "user_id": withdrawal.user_id,
                    "user_email": user_email,
                    "user_name": user_name,
                    "amount_minor": withdrawal.amount_minor,
                    "fee_minor": withdrawal.fee_minor,
                    "net_amount_minor": withdrawal.amount_minor,  # User receives amount, fee is separate
                    "currency": withdrawal.currency,
                    "status": withdrawal.status,
                    "method": withdrawal.method,
                    "stripe_payout_id": withdrawal.stripe_payout_id,
                    "stripe_transfer_id": withdrawal.stripe_transfer_id,
                    "stripe_balance_txn_id": withdrawal.stripe_balance_txn_id,
                    "bank_details": bank_details,
                    "admin_notes": withdrawal.admin_notes,
                    "requested_at": withdrawal.requested_at.isoformat() if withdrawal.requested_at else None,
                    "processed_at": withdrawal.processed_at.isoformat() if withdrawal.processed_at else None,
                })
            except Exception as e:
                logger.error(f"Error processing withdrawal record {withdrawal.id}: {str(e)}", exc_info=True)
                # Continue with the next record
        
        return {
            "withdrawals": formatted_withdrawals,
            "total_count": total_count,
            "summary": {
                "by_method": {
                    "instant": instant_count,
                    "standard": standard_count
                },
                "by_status": {
                    "pending": pending_count,
                    "processing": processing_count,
                    "paid": paid_count,
                    "failed": failed_count
                }
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving withdrawal requests: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) 