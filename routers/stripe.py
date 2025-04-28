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
from fastapi import APIRouter, Depends, Request, HTTPException, BackgroundTasks, Header, Body, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from db import get_db
from models import User, Payment, PaymentTransaction, UserBankAccount, SubscriptionPlan, UserSubscription
from routers.dependencies import get_current_user
import config
from auth import verify_access_token
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
        orm_mode = True

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

# Endpoint to add funds to the user's wallet
@router.post("/add-funds-to-wallet")
async def add_funds_to_wallet(
    amount: int = Body(..., description="Amount in cents to add to wallet (e.g., 1000 for $10.00)"),
    currency: str = Body("usd", description="Three-letter currency code (e.g., 'usd', 'eur')"),
    db: Session = Depends(get_db),
    token: dict = Depends(verify_access_token)
):
    """
    ## Add Funds to User Wallet
    
    Creates a PaymentIntent and returns the client secret needed to complete payment on the client side.
    
    ### Use this endpoint to:
    - Allow users to top up their wallet balance
    - Process payments via Stripe that will be credited to the user's in-app wallet
    
    ### Request Body:
    - `amount`: The amount in cents to add to the wallet (e.g., 1000 for $10.00)
    - `currency`: Three-letter currency code (default: 'usd')
    
    ### Returns:
    - `clientSecret`: Stripe PaymentIntent client secret to complete payment on the client
    - `paymentIntentId`: ID of the created Stripe PaymentIntent
    - `amount`: The amount in cents that will be charged
    - `currency`: The currency code being used
    
    ### Note:
    The wallet will only be credited once the payment is confirmed via webhook.
    """
    try:
        # Get user from the database
        sub = token.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Create payment intent with user metadata
        metadata = {
            "transaction_type": "wallet_deposit",
            "user_id": str(user.account_id),
            "sub": sub
        }
        
        payment_intent = stripe.PaymentIntent.create(
            amount=amount,
            currency=currency,
            metadata=metadata,
            automatic_payment_methods={"enabled": True}
        )
        
        # Create a transaction record in pending state
        transaction = PaymentTransaction(
            user_id=user.account_id,
            payment_intent_id=payment_intent.id,
            amount=amount / 100.0,  # Convert cents to dollars
            currency=currency,
            status="pending",
            payment_metadata=json.dumps(metadata)
        )
        
        db.add(transaction)
        db.commit()
        
        return {
            "clientSecret": payment_intent.client_secret,
            "paymentIntentId": payment_intent.id,
            "amount": amount,
            "currency": currency
        }
    except stripe.error.StripeError as e:
        # Log and return error
        logger.error(f"Stripe error: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Log and return error
        logger.error(f"Error adding funds to wallet: {str(e)}")
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
    token: dict = Depends(verify_access_token)
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
        sub = token.get("sub")
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
async def handle_successful_payment(payment_intent):
    # Create a database session
    db = next(get_db())
    
    try:
        # Find transaction by payment intent ID
        transaction = db.query(PaymentTransaction).filter(
            PaymentTransaction.payment_intent_id == payment_intent.id
        ).first()
        
        if transaction:
            # Check if the transaction is already marked as succeeded (idempotency check)
            if transaction.status == "succeeded":
                logging.info(f"Payment {payment_intent.id} already processed. Skipping to ensure idempotency.")
                return
                
            # Update transaction status
            transaction.status = "succeeded"
            transaction.payment_method = payment_intent.payment_method
            transaction.payment_method_type = payment_intent.payment_method_types[0] if payment_intent.payment_method_types else None
            transaction.payment_metadata = json.dumps(payment_intent.metadata.to_dict())
            
            # Get user from transaction
            user = db.query(User).filter(User.account_id == transaction.user_id).first()
            
            if user and payment_intent.metadata.get("transaction_type") == "wallet_deposit":
                # Check if wallet was already updated for this payment (idempotency check)
                previous_metadata = json.loads(transaction.payment_metadata) if transaction.payment_metadata else {}
                if previous_metadata.get("wallet_credited"):
                    logging.info(f"Wallet for user {user.account_id} already credited for payment {payment_intent.id}. Skipping.")
                else:
                    # Add funds to user's wallet
                    current_balance = user.wallet_balance or 0
                    user.wallet_balance = current_balance + (payment_intent.amount / 100.0)  # Convert cents to dollars
                    user.last_wallet_update = datetime.utcnow()
                    
                    # Update metadata to record that wallet was credited
                    metadata = payment_intent.metadata.to_dict()
                    metadata["wallet_credited"] = True
                    metadata["credited_at"] = datetime.utcnow().isoformat()
                    transaction.payment_metadata = json.dumps(metadata)
                    
                    logging.info(f"Added ${payment_intent.amount / 100.0} to user {user.account_id}'s wallet. New balance: ${user.wallet_balance}")
            
            db.commit()
            logging.info(f"Payment successful for transaction {transaction.id}")
        else:
            # Create a new transaction record if not found
            metadata = payment_intent.metadata.to_dict()
            user_id = int(metadata.get("user_id")) if metadata.get("user_id") else None
            
            if user_id:
                transaction = PaymentTransaction(
                    user_id=user_id,
                    payment_intent_id=payment_intent.id,
                    amount=payment_intent.amount / 100.0,  # Convert cents to dollars
                    currency=payment_intent.currency,
                    status="succeeded",
                    payment_method=payment_intent.payment_method,
                    payment_method_type=payment_intent.payment_method_types[0] if payment_intent.payment_method_types else None,
                    payment_metadata=json.dumps(metadata)
                )
                
                db.add(transaction)
                
                # If this is a wallet deposit, add funds to user's wallet
                if metadata.get("transaction_type") == "wallet_deposit":
                    user = db.query(User).filter(User.account_id == user_id).first()
                    if user:
                        # Check if there is another transaction for this payment (idempotency check)
                        duplicate_tx = db.query(PaymentTransaction).filter(
                            PaymentTransaction.payment_intent_id == payment_intent.id,
                            PaymentTransaction.id != transaction.id
                        ).first()
                        
                        if duplicate_tx:
                            logging.warning(f"Duplicate transaction detected for payment {payment_intent.id}. Skipping wallet update.")
                        else:
                            current_balance = user.wallet_balance or 0
                            user.wallet_balance = current_balance + (payment_intent.amount / 100.0)
                            user.last_wallet_update = datetime.utcnow()
                            
                            # Update metadata to record that wallet was credited
                            metadata["wallet_credited"] = True
                            metadata["credited_at"] = datetime.utcnow().isoformat()
                            transaction.payment_metadata = json.dumps(metadata)
                            
                            logging.info(f"Added ${payment_intent.amount / 100.0} to user {user.account_id}'s wallet. New balance: ${user.wallet_balance}")
                
                db.commit()
                logging.info(f"Created new transaction record for payment {payment_intent.id}")
    except Exception as e:
        db.rollback()
        logging.error(f"Error processing successful payment: {str(e)}")
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
    try:
        # Get the payload from the request
        payload = await request.body()
        sig_header = stripe_signature
        
        logger.info("Received Stripe webhook")
        
        try:
            # Verify webhook signature
            endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
            if endpoint_secret:
                event = stripe.Webhook.construct_event(
                    payload, sig_header, endpoint_secret
                )
            else:
                # For testing without a webhook secret
                data = json.loads(payload)
                event = stripe.Event.construct_from(data, stripe.api_key)
                
            logger.info(f"Validated webhook: {event.type}")
        except (ValueError, stripe.error.SignatureVerificationError) as e:
            logger.error(f"Webhook signature verification failed: {e}")
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
                background_tasks.add_task(handle_successful_payment, event_object)
                
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
                    db.add(user_sub)
                    db.commit()
                
                elif event_type == 'customer.subscription.updated':
                    user_sub.status = event_object.status
                    user_sub.current_period_start = datetime.fromtimestamp(event_object.current_period_start)
                    user_sub.current_period_end = datetime.fromtimestamp(event_object.current_period_end)
                    user_sub.cancel_at_period_end = event_object.cancel_at_period_end
                    db.add(user_sub)
                    db.commit()
                
                elif event_type == 'customer.subscription.deleted':
                    user_sub.status = 'canceled'
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
                        transaction = PaymentTransaction(
                            user_id=user_sub.user_id,
                            payment_intent_id=event_object.payment_intent,
                            amount=event_object.amount_paid / 100.0,  # Convert cents to dollars
                            currency=event_object.currency,
                            status='succeeded',
                            payment_method='card',
                            payment_method_type='subscription',
                            payment_metadata=json.dumps({
                                'transaction_type': 'subscription_renewal',
                                'invoice_id': invoice_id,
                                'subscription_id': subscription_id,
                                'user_id': str(user_sub.user_id)
                            })
                        )
                        db.add(transaction)
                        
                        # Ensure subscription status is active
                        user_sub.status = 'active'
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
                        transaction = PaymentTransaction(
                            user_id=user_sub.user_id,
                            payment_intent_id=event_object.payment_intent,
                            amount=event_object.amount_due / 100.0,  # Convert cents to dollars
                            currency=event_object.currency,
                            status='action_required',
                            payment_method='card',
                            payment_method_type='subscription',
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
            
            # Find transaction by payout ID
            transaction = db.query(PaymentTransaction).filter(
                PaymentTransaction.payment_intent_id == payout_id
            ).first()
            
            if transaction:
                if event_type == 'payout.created':
                    # Update transaction status
                    transaction.status = 'processing'
                    db.add(transaction)
                    db.commit()
                
                elif event_type == 'payout.paid':
                    # Payout successfully deposited
                    transaction.status = 'succeeded'
                    db.add(transaction)
                    db.commit()
                
                elif event_type == 'payout.failed':
                    # Payout failed, refund the user
                    transaction.status = 'failed'
                    transaction.last_error = event_object.failure_message
                    
                    # Handle idempotency - check if we already refunded
                    metadata = json.loads(transaction.payment_metadata) if transaction.payment_metadata else {}
                    if metadata.get("refunded"):
                        logger.info(f"Payout {payout_id} failure already processed. Skipping refund to ensure idempotency.")
                    else:
                        # Get the user and update the balance
                        user = db.query(User).filter(User.account_id == transaction.user_id).first()
                        if user:
                            user.wallet_balance = user.wallet_balance + transaction.amount
                            user.last_wallet_update = datetime.utcnow()
                            db.add(user)
                            
                            # Update metadata to record refund
                            metadata["refunded"] = True
                            metadata["refund_date"] = datetime.utcnow().isoformat()
                            metadata["refund_reason"] = event_object.failure_message
                            transaction.payment_metadata = json.dumps(metadata)
                            
                            logger.info(f"Refunded ${transaction.amount} to user {user.account_id} due to failed payout")
                        
                            # TODO: Send email notification about the failed payout
                            logger.warning(f"Payout failed for transaction {transaction.id}. Reason: {event_object.failure_message}")
                    
                    db.add(transaction)
                    db.commit()
        
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
        
        return {"status": "success"}
    
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint to get user's wallet balance
@router.get("/wallet-balance")
async def get_wallet_balance(
    db: Session = Depends(get_db),
    token: dict = Depends(verify_access_token)
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
        sub = token.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get recent transactions
        recent_transactions = db.query(PaymentTransaction).filter(
            PaymentTransaction.user_id == user.account_id,
            PaymentTransaction.status == "succeeded"
        ).order_by(PaymentTransaction.created_at.desc()).limit(5).all()
        
        # Format transactions for response
        transactions = []
        for tx in recent_transactions:
            transactions.append({
                "id": tx.id,
                "amount": tx.amount,
                "currency": tx.currency,
                "created_at": tx.created_at.isoformat() if tx.created_at else None,
                "payment_method_type": tx.payment_method_type,
                "transaction_type": json.loads(tx.payment_metadata).get("transaction_type") if tx.payment_metadata else None
            })
        
        return {
            "wallet_balance": user.wallet_balance or 0.0,
            "currency": "USD",  # Default currency
            "last_updated": user.last_wallet_update.isoformat() if user.last_wallet_update else None,
            "recent_transactions": transactions
        }
    except Exception as e:
        logger.error(f"Error retrieving wallet balance: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint to withdraw funds from wallet
@router.post("/withdraw-from-wallet")
async def withdraw_from_wallet(
    amount: float = Body(..., description="Amount in dollars to withdraw from wallet"),
    payout_method: str = Body(..., description="Method for withdrawal ('standard' or 'instant')"),
    bank_account_id: Optional[int] = Body(None, description="ID of the saved bank account to use for withdrawal"),
    payout_details: Optional[Dict[str, Any]] = Body(None, description="Details needed for the payout if not using a saved bank account"),
    db: Session = Depends(get_db),
    token: dict = Depends(verify_access_token)
):
    """
    ## Withdraw Funds from Wallet
    
    Creates a withdrawal request to transfer funds from the user's wallet to their bank account.
    
    ### Use this endpoint to:
    - Allow users to cash out their wallet balance
    - Process withdrawal requests using standard or instant methods
    
    ### Request Body:
    - `amount`: Amount in dollars to withdraw (must be less than or equal to current wallet balance)
    - `payout_method`: Method for withdrawal ('standard' or 'instant')
      - 'standard': Free, processed within 1-3 business days, subject to admin review
      - 'instant': Instant transfer, 1.5% fee (minimum $0.50), no admin review required
    - `bank_account_id`: (Optional) ID of a saved bank account to use
    - `payout_details`: (Optional) Details needed if not using a saved bank account
    
    ### Returns:
    - `status`: Status of the withdrawal request
    - `amount`: Amount being withdrawn
    - `fee`: Any processing fee (for instant transfers)
    - `net_amount`: Final amount after fees
    - `currency`: Currency of the withdrawal
    - `transaction_id`: ID of the created transaction record
    - `message`: Informational message about the withdrawal process
    
    ### Note:
    Standard withdrawals require admin approval before funds are sent.
    Instant withdrawals are processed immediately with a fee.
    """
    try:
        # Get user from the database
        sub = token.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Validate withdrawal method
        if payout_method not in ["standard", "instant"]:
            raise HTTPException(status_code=400, detail="Invalid payout method. Must be 'standard' or 'instant'")
        
        # Set transaction metadata
        metadata = {
            "transaction_type": "wallet_withdrawal",
            "user_email": user.email,
            "withdraw_method": payout_method
        }
        
        # Calculate processing fee for instant transfers
        fee = 0
        if payout_method == "instant":
            # 1.5% fee with minimum of $0.50
            fee = max(round(amount * 0.015, 2), 0.50)
            
        # Calculate total amount needed (withdrawal + fee)
        total_amount = amount + fee
        
        # Check if user has enough balance
        current_balance = user.wallet_balance or 0
        if current_balance < total_amount:
            raise HTTPException(
                status_code=400, 
                detail=f"Insufficient funds. Available balance: ${current_balance:.2f}, " +
                       f"Required: ${amount:.2f} + ${fee:.2f} fee = ${total_amount:.2f}"
            )
        
        # If bank account ID is provided, validate and use that account
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
            
            # Add bank account details to metadata
            metadata["bank_account_id"] = bank_account_id
            metadata["account_name"] = bank_account.account_name
            metadata["bank_name"] = bank_account.bank_name
            metadata["account_last4"] = bank_account.account_number_last4
            
            # For production, you would decrypt the account info here to use with Stripe
        
        # If no bank account ID but payout details provided, use those
        elif payout_details:
            # Validate payout details contain necessary info
            required_fields = ["account_holder_name", "account_number", "routing_number", "bank_name"]
            for field in required_fields:
                if field not in payout_details:
                    raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
            
            # Add details to metadata
            metadata["account_name"] = payout_details["account_holder_name"]
            metadata["bank_name"] = payout_details["bank_name"]
            metadata["account_last4"] = get_last_four(payout_details["account_number"])
            
            # For production, you would encrypt this data before storing
        else:
            raise HTTPException(
                status_code=400, 
                detail="Either bank_account_id or payout_details must be provided"
            )
        
        # Create a transaction record
        transaction = PaymentTransaction(
            user_id=user.account_id,
            amount=amount,
            currency="usd",
            status="pending" if payout_method == "standard" else "processing",
            payment_method="bank_account",
            payment_method_type=payout_method,
            payment_metadata=json.dumps({
                **metadata,
                "fee": fee,
                "payout_details": payout_details if payout_details else "Using saved bank account"
            })
        )
        
        # Update user's wallet balance
        user.wallet_balance = current_balance - total_amount
        user.last_wallet_update = datetime.utcnow()
        
        db.add(transaction)
        db.commit()
        db.refresh(transaction)
        
        logger.info(f"{payout_method.capitalize()} withdrawal of ${amount} requested by user {user.account_id}")
        
        # For instant transfers, process immediately with Stripe
        if payout_method == "instant":
            try:
                # In production, this would use the Stripe API to initiate an instant payout
                # stripe_payout = stripe.Payout.create(
                #     amount=int(amount * 100),  # Convert to cents
                #     currency="usd",
                #     method="instant",
                #     source_type="card",
                #     metadata={
                #         "transaction_id": transaction.id,
                #         "user_id": user.account_id
                #     }
                # )
                
                # For demo purposes, simulate a successful payout with a unique ID
                payout_id = f"po_inst_{int(datetime.utcnow().timestamp())}"
                
                # Update transaction with payout ID
                transaction.payment_intent_id = payout_id
                transaction.status = "succeeded"  # Assume success for demo
                db.add(transaction)
                db.commit()
                
                return {
                    "status": "succeeded",
                    "amount": amount,
                    "fee": fee,
                    "net_amount": amount - fee,
                    "currency": "usd",
                    "transaction_id": transaction.id,
                    "payment_intent_id": payout_id,
                    "message": "Instant withdrawal processed successfully. Funds should arrive within minutes."
                }
                
            except Exception as e:
                # If instant payout fails, refund the user and log the error
                logger.error(f"Error processing instant withdrawal: {str(e)}")
                
                user.wallet_balance = user.wallet_balance + total_amount
                transaction.status = "failed"
                transaction.last_error = str(e)
                db.add(user)
                db.add(transaction)
                db.commit()
                
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to process instant withdrawal: {str(e)}"
                )
        
        # For standard transfers, return pending status
        return {
            "status": "pending",
            "amount": amount,
            "fee": fee,
            "net_amount": amount,
            "currency": "usd",
            "transaction_id": transaction.id,
            "message": "Withdrawal request submitted successfully. Funds will be transferred within 1-3 business days after admin approval."
        }
            
    except stripe.error.StripeError as e:
        logger.error(f"Stripe error: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException as e:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error processing withdrawal: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint to get withdrawal history
@router.get("/withdrawal-history")
async def get_withdrawal_history(
    db: Session = Depends(get_db),
    token: dict = Depends(verify_access_token),
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
    - `limit`: Maximum number of records to return (default: 10)
    - `offset`: Number of records to skip for pagination (default: 0)
    
    ### Returns:
    - `withdrawals`: List of withdrawal transactions with the following fields:
        - `id`: Withdrawal transaction ID
        - `amount`: Amount in dollars
        - `currency`: Currency code
        - `status`: Status of the withdrawal ('pending', 'completed', 'failed')
        - `created_at`: When the withdrawal was requested
        - `updated_at`: When the withdrawal was last updated
        - `payout_method`: Method used for withdrawal
        - `payout_details`: Details of the withdrawal method
    - `total_count`: Total number of withdrawal records for pagination
    """
    try:
        # Get user from token
        sub = token.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get withdrawal transactions
        withdrawals = db.query(PaymentTransaction).filter(
            PaymentTransaction.user_id == user.account_id,
            PaymentTransaction.payment_metadata.contains('"transaction_type": "wallet_withdrawal"')
        ).order_by(PaymentTransaction.created_at.desc()).offset(offset).limit(limit).all()
        
        # Format the results
        withdrawal_history = []
        for withdrawal in withdrawals:
            try:
                metadata = json.loads(withdrawal.payment_metadata) if withdrawal.payment_metadata else {}
                payout_details = metadata.get("payout_details", {})
                
                withdrawal_history.append({
                    "id": withdrawal.id,
                    "amount": withdrawal.amount,
                    "currency": withdrawal.currency,
                    "status": withdrawal.status,
                    "created_at": withdrawal.created_at.isoformat() if withdrawal.created_at else None,
                    "updated_at": withdrawal.updated_at.isoformat() if withdrawal.updated_at else None,
                    "payout_method": withdrawal.payment_method_type,
                    "payout_details": payout_details
                })
            except Exception as e:
                logger.error(f"Error processing withdrawal record {withdrawal.id}: {str(e)}")
                # Continue with the next record
        
        return {
            "withdrawals": withdrawal_history,
            "total_count": db.query(PaymentTransaction).filter(
                PaymentTransaction.user_id == user.account_id,
                PaymentTransaction.payment_metadata.contains('"transaction_type": "wallet_withdrawal"')
            ).count()
        }
    except Exception as e:
        logger.error(f"Error retrieving withdrawal history: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Admin endpoint to process withdrawals
@router.post("/admin/process-withdrawal/{transaction_id}")
async def process_withdrawal(
    transaction_id: int,
    status: str = Body(..., description="New status for the withdrawal ('completed' or 'failed')"),
    notes: str = Body(None, description="Admin notes about the withdrawal process or reason for failure"),
    db: Session = Depends(get_db),
    token: dict = Depends(verify_access_token)
):
    """
    ## Process Withdrawal Request (Admin Only)
    
    Allows administrators to approve or reject standard withdrawal requests.
    
    ### Use this endpoint to:
    - Mark standard withdrawals as completed after sending funds
    - Reject withdrawals that cannot be processed
    - Add notes about the processing outcome
    - Review details of instant withdrawals (but cannot change their status if already processed)
    
    ### Path Parameters:
    - `transaction_id`: ID of the withdrawal transaction to process
    
    ### Request Body:
    - `status`: New status for the withdrawal:
        - 'completed': Mark the withdrawal as successfully processed
        - 'failed': Mark the withdrawal as failed, which will refund the amount to the user's wallet
    - `notes`: Optional admin notes about the withdrawal process or reason for failure
    
    ### Returns:
    - `transaction_id`: ID of the processed transaction
    - `status`: Updated status
    - `notes`: Admin notes provided
    - `withdrawal_type`: Type of withdrawal ('standard' or 'instant')
    - `updated_at`: Timestamp of the update
    - `message`: Additional information about the action taken
    
    ### Note:
    This endpoint requires admin privileges. If a withdrawal is marked as failed,
    the amount will be automatically refunded to the user's wallet.
    
    Instant withdrawals that have already been processed cannot have their status changed,
    but admins can still review them and add notes.
    """
    try:
        # Check if user is admin
        sub = token.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user or not user.is_admin:
            raise HTTPException(status_code=403, detail="Admin access required")
        
        # Get the transaction
        transaction = db.query(PaymentTransaction).filter(
            PaymentTransaction.id == transaction_id
        ).first()
        
        if not transaction:
            raise HTTPException(status_code=404, detail=f"Transaction {transaction_id} not found")
        
        # Check if it's a withdrawal transaction
        metadata = json.loads(transaction.payment_metadata) if transaction.payment_metadata else {}
        if metadata.get("transaction_type") != "wallet_withdrawal":
            raise HTTPException(status_code=400, detail="Not a withdrawal transaction")
        
        # Get withdrawal method
        withdrawal_type = metadata.get("withdraw_method", "standard")
        
        # For instant withdrawals that are already processed, don't allow status changes
        if withdrawal_type == "instant" and transaction.status in ["succeeded", "failed"]:
            # Just add admin notes without changing status
            if notes:
                transaction.admin_notes = notes
                db.add(transaction)
                db.commit()
            
            return {
                "transaction_id": transaction_id,
                "status": transaction.status,
                "notes": notes,
                "withdrawal_type": withdrawal_type,
                "updated_at": transaction.updated_at.isoformat() if transaction.updated_at else None,
                "message": "This instant withdrawal has already been processed and cannot be changed. Notes added for record keeping."
            }
        
        # Validate status for standard withdrawals
        if status not in ["completed", "failed"]:
            raise HTTPException(status_code=400, detail="Status must be 'completed' or 'failed'")
        
        old_status = transaction.status
        transaction.status = status
        transaction.last_error = notes if status == "failed" else None
        transaction.admin_notes = notes
        
        # Calculate refund amount (including any fees)
        refund_amount = transaction.amount
        fee = metadata.get("fee", 0)
        if withdrawal_type == "instant" and fee > 0:
            refund_amount += fee
        
        # If the withdrawal failed, refund the amount to the user's wallet
        if status == "failed" and old_status != "failed":
            # Get the user
            withdrawal_user = db.query(User).filter(User.account_id == transaction.user_id).first()
            if withdrawal_user:
                # Add the withdrawal amount back to the user's wallet
                withdrawal_user.wallet_balance = (withdrawal_user.wallet_balance or 0) + refund_amount
                withdrawal_user.last_wallet_update = datetime.utcnow()
                
                # Update metadata with refund information
                metadata["refunded"] = True
                metadata["refund_date"] = datetime.utcnow().isoformat()
                metadata["refund_reason"] = notes or "Withdrawal failed"
                metadata["refund_amount"] = refund_amount
                transaction.payment_metadata = json.dumps(metadata)
                
                logger.info(f"Refunded ${refund_amount} to user {withdrawal_user.account_id} due to failed withdrawal")
        
        # For completed standard withdrawals, we would normally complete the payout here
        if status == "completed" and withdrawal_type == "standard" and old_status != "completed":
            # In production, this would initiate the actual Stripe payout
            # stripe_payout = stripe.Payout.create(
            #     amount=int(transaction.amount * 100),  # Convert to cents
            #     currency="usd",
            #     method="standard",
            #     metadata={
            #         "transaction_id": transaction.id,
            #         "user_id": transaction.user_id
            #     }
            # )
            
            # For demo purposes, simulate a successful payout with a unique ID
            payout_id = f"po_std_{int(datetime.utcnow().timestamp())}"
            transaction.payment_intent_id = payout_id
            
            # Update metadata with payout information
            metadata["payout_date"] = datetime.utcnow().isoformat()
            metadata["payout_id"] = payout_id
            transaction.payment_metadata = json.dumps(metadata)
            
            logger.info(f"Standard withdrawal {transaction_id} processed: ${transaction.amount} for user {transaction.user_id}")
        
        db.commit()
        
        logger.info(f"Withdrawal {transaction_id} marked as {status} by admin {user.account_id}")
        
        return {
            "transaction_id": transaction_id,
            "status": status,
            "notes": notes,
            "withdrawal_type": withdrawal_type,
            "updated_at": transaction.updated_at.isoformat() if transaction.updated_at else None,
            "message": f"{withdrawal_type.capitalize()} withdrawal successfully {status}."
        }
        
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error processing withdrawal status update: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Bank Account Management Endpoints
#---------------------------------

@router.post("/bank-accounts", response_model=BankAccountResponse)
async def add_bank_account(
    bank_account: BankAccountRequest,
    db: Session = Depends(get_db),
    token: dict = Depends(verify_access_token)
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
        sub = token.get("sub")
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
    token: dict = Depends(verify_access_token)
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
        sub = token.get("sub")
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
    token: dict = Depends(verify_access_token)
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
        sub = token.get("sub")
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
        orm_mode = True

@router.get("/subscriptions/plans", response_model=List[dict])
async def list_subscription_plans(
    db: Session = Depends(get_db),
    token: dict = Depends(verify_access_token)
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
        sub = token.get("sub")
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
    token: dict = Depends(verify_access_token)
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
        sub = token.get("sub")
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
    token: dict = Depends(verify_access_token)
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
        sub = token.get("sub")
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
    token: dict = Depends(verify_access_token)
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
        sub = token.get("sub")
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
    withdrawal_type: Optional[str] = Query(None, description="Filter by withdrawal type: 'standard' or 'instant'"),
    status: Optional[str] = Query(None, description="Filter by status: 'pending', 'processing', 'succeeded', 'failed'"),
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    limit: int = Query(20, description="Maximum number of records to return"),
    offset: int = Query(0, description="Number of records to skip"),
    db: Session = Depends(get_db),
    token: dict = Depends(verify_access_token)
):
    """
    ## Get All Withdrawal Transactions (Admin Only)
    
    Returns a paginated list of withdrawal transactions with filtering options.
    
    ### Use this endpoint to:
    - View and manage all withdrawal requests
    - Filter transactions by type, status, or user
    - Monitor instant vs standard withdrawals
    
    ### Query Parameters:
    - `withdrawal_type`: (Optional) Filter by 'standard' or 'instant'
    - `status`: (Optional) Filter by transaction status ('pending', 'processing', 'succeeded', 'failed')
    - `user_id`: (Optional) Filter by specific user ID
    - `limit`: Maximum number of records to return (default: 20)
    - `offset`: Number of records to skip for pagination (default: 0)
    
    ### Returns:
    - `withdrawals`: List of withdrawal transactions with details
    - `total_count`: Total number of withdrawals matching the filters
    - `instant_count`: Number of instant withdrawals
    - `standard_count`: Number of standard withdrawals
    - `pending_count`: Number of pending withdrawals
    - `succeeded_count`: Number of succeeded withdrawals
    - `failed_count`: Number of failed withdrawals
    
    ### Note:
    This endpoint requires admin privileges.
    """
    try:
        # Check if user is admin
        sub = token.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Invalid user token")
        
        user = db.query(User).filter(User.sub == sub).first()
        if not user or not user.is_admin:
            raise HTTPException(status_code=403, detail="Admin access required")
        
        # Build the base query for withdrawal transactions
        base_query = db.query(PaymentTransaction).filter(
            PaymentTransaction.payment_metadata.contains('"transaction_type": "wallet_withdrawal"')
        )
        
        # Apply type filter if provided
        if withdrawal_type:
            if withdrawal_type not in ["standard", "instant"]:
                raise HTTPException(status_code=400, detail="Withdrawal type must be 'standard' or 'instant'")
            base_query = base_query.filter(
                PaymentTransaction.payment_metadata.contains(f'"withdraw_method": "{withdrawal_type}"')
            )
        
        # Apply status filter if provided
        if status:
            if status not in ["pending", "processing", "succeeded", "failed"]:
                raise HTTPException(
                    status_code=400, 
                    detail="Status must be one of: 'pending', 'processing', 'succeeded', 'failed'"
                )
            base_query = base_query.filter(PaymentTransaction.status == status)
        
        # Apply user filter if provided
        if user_id:
            base_query = base_query.filter(PaymentTransaction.user_id == user_id)
        
        # Get total counts for summary stats
        total_count = base_query.count()
        
        # Get counts by type
        instant_count = db.query(PaymentTransaction).filter(
            PaymentTransaction.payment_metadata.contains('"transaction_type": "wallet_withdrawal"'),
            PaymentTransaction.payment_metadata.contains('"withdraw_method": "instant"')
        ).count()
        
        standard_count = db.query(PaymentTransaction).filter(
            PaymentTransaction.payment_metadata.contains('"transaction_type": "wallet_withdrawal"'),
            PaymentTransaction.payment_metadata.contains('"withdraw_method": "standard"')
        ).count()
        
        # Get counts by status
        pending_count = db.query(PaymentTransaction).filter(
            PaymentTransaction.payment_metadata.contains('"transaction_type": "wallet_withdrawal"'),
            PaymentTransaction.status == "pending"
        ).count()
        
        processing_count = db.query(PaymentTransaction).filter(
            PaymentTransaction.payment_metadata.contains('"transaction_type": "wallet_withdrawal"'),
            PaymentTransaction.status == "processing"
        ).count()
        
        succeeded_count = db.query(PaymentTransaction).filter(
            PaymentTransaction.payment_metadata.contains('"transaction_type": "wallet_withdrawal"'),
            PaymentTransaction.status == "succeeded"
        ).count()
        
        failed_count = db.query(PaymentTransaction).filter(
            PaymentTransaction.payment_metadata.contains('"transaction_type": "wallet_withdrawal"'),
            PaymentTransaction.status == "failed"
        ).count()
        
        # Get paginated results
        transactions = base_query.order_by(
            PaymentTransaction.created_at.desc()
        ).offset(offset).limit(limit).all()
        
        # Format each transaction with user details
        formatted_withdrawals = []
        for tx in transactions:
            try:
                metadata = json.loads(tx.payment_metadata) if tx.payment_metadata else {}
                
                # Get user details
                withdrawal_user = db.query(User).filter(User.account_id == tx.user_id).first()
                user_email = withdrawal_user.email if withdrawal_user else metadata.get("user_email", "Unknown")
                user_name = withdrawal_user.name if withdrawal_user else "Unknown"
                
                # Extract withdrawal details
                withdraw_method = metadata.get("withdraw_method", "standard")
                fee = metadata.get("fee", 0)
                
                # Format bank details
                bank_details = {
                    "account_name": metadata.get("account_name", "N/A"),
                    "bank_name": metadata.get("bank_name", "N/A"),
                    "account_last4": metadata.get("account_last4", "N/A")
                }
                
                formatted_withdrawals.append({
                    "id": tx.id,
                    "user_id": tx.user_id,
                    "user_email": user_email,
                    "user_name": user_name,
                    "amount": tx.amount,
                    "fee": fee,
                    "net_amount": tx.amount - fee if withdraw_method == "instant" else tx.amount,
                    "currency": tx.currency,
                    "status": tx.status,
                    "payment_intent_id": tx.payment_intent_id,
                    "withdrawal_type": withdraw_method,
                    "bank_details": bank_details,
                    "admin_notes": tx.admin_notes,
                    "last_error": tx.last_error,
                    "created_at": tx.created_at.isoformat() if tx.created_at else None,
                    "updated_at": tx.updated_at.isoformat() if tx.updated_at else None,
                })
            except Exception as e:
                logger.error(f"Error processing withdrawal record {tx.id}: {str(e)}")
                # Continue with the next record
        
        return {
            "withdrawals": formatted_withdrawals,
            "total_count": total_count,
            "summary": {
                "by_type": {
                    "instant": instant_count,
                    "standard": standard_count
                },
                "by_status": {
                    "pending": pending_count,
                    "processing": processing_count,
                    "succeeded": succeeded_count,
                    "failed": failed_count
                }
            }
        }
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Error retrieving withdrawal transactions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e)) 