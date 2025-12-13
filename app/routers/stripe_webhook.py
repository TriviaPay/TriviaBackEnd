"""
Stripe Webhook Router - Handles Stripe webhook events
"""
from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from datetime import datetime
from app.db import get_async_db
from app.services.stripe_service import verify_webhook_signature
from app.models.wallet import WalletTransaction, WithdrawalRequest
from app.models.user import User
from app.services.wallet_service import adjust_wallet_balance
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stripe/webhook", tags=["Stripe Webhooks"])


@router.post("")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="stripe-signature"),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Handle Stripe webhook events.
    
    Verifies webhook signature and processes events:
    - account.updated: Update Connect account status
    - transfer.paid: Update withdrawal request status
    - transfer.failed: Mark withdrawal as failed and refund
    - payout.paid: Update withdrawal request status
    - payout.failed: Mark withdrawal as failed and refund
    
    TODO: Add more event handlers as needed.
    """
    if not stripe_signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header"
        )
    
    # Get raw body for signature verification
    body = await request.body()
    
    try:
        # Verify webhook signature
        event = verify_webhook_signature(body, stripe_signature)
    except ValueError as e:
        logger.error(f"Webhook signature verification failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid webhook signature: {str(e)}"
        )
    
    event_type = event['type']
    event_id = event['id']
    livemode = event['livemode']
    
    logger.info(f"Received Stripe webhook: {event_type} (event_id: {event_id}, livemode: {livemode})")
    
    # Check idempotency - see if we've already processed this event
    stmt = select(WalletTransaction).where(WalletTransaction.event_id == event_id)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        logger.info(f"Event {event_id} already processed, skipping")
        return {"status": "already_processed", "event_id": event_id}
    
    try:
        # Handle different event types
        if event_type == "account.updated":
            # Handle Connect account updates
            account = event['data']['object']
            account_id = account.get('id')
            charges_enabled = account.get('charges_enabled', False)
            payouts_enabled = account.get('payouts_enabled', False)
            details_submitted = account.get('details_submitted', False)
            
            # Find user with this Connect account ID
            user_stmt = select(User).where(User.stripe_connect_account_id == account_id)
            user_result = await db.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            
            if user:
                # Update user's Connect account status flags if they exist
                if hasattr(user, 'stripe_charges_enabled'):
                    user.stripe_charges_enabled = charges_enabled
                if hasattr(user, 'stripe_payouts_enabled'):
                    user.stripe_payouts_enabled = payouts_enabled
                if hasattr(user, 'stripe_details_submitted'):
                    user.stripe_details_submitted = details_submitted
                
                await db.commit()
                logger.info(f"Updated Connect account status for user {user.account_id}: charges={charges_enabled}, payouts={payouts_enabled}, details={details_submitted}")
            else:
                logger.warning(f"User not found for Connect account {account_id}")
            
        elif event_type in ["transfer.paid", "payout.paid"]:
            # Handle successful payouts
            transfer = event['data']['object']
            transfer_id = transfer.get('id')
            
            # Find withdrawal request by payout/transfer ID
            withdrawal_stmt = select(WithdrawalRequest).where(
                WithdrawalRequest.stripe_payout_id == transfer_id
            )
            withdrawal_result = await db.execute(withdrawal_stmt)
            withdrawal = withdrawal_result.scalar_one_or_none()
            
            if withdrawal:
                if withdrawal.status != 'paid':
                    withdrawal.status = 'paid'
                    withdrawal.processed_at = datetime.utcnow()
                    await db.commit()
                    logger.info(f"Updated withdrawal {withdrawal.id} to paid status for payout {transfer_id}")
                else:
                    logger.info(f"Withdrawal {withdrawal.id} already marked as paid")
            else:
                logger.warning(f"Withdrawal request not found for payout/transfer {transfer_id}")
            
        elif event_type in ["transfer.failed", "payout.failed"]:
            # Handle failed payouts - need to refund wallet
            transfer = event['data']['object']
            transfer_id = transfer.get('id')
            amount = transfer.get('amount', 0)
            
            # Find withdrawal request by payout/transfer ID
            withdrawal_stmt = select(WithdrawalRequest).where(
                WithdrawalRequest.stripe_payout_id == transfer_id
            )
            withdrawal_result = await db.execute(withdrawal_stmt)
            withdrawal = withdrawal_result.scalar_one_or_none()
            
            if withdrawal:
                if withdrawal.status != 'failed':
                    # Refund the wallet (amount + fee)
                    refund_amount = withdrawal.amount_minor + withdrawal.fee_minor
                    
                    try:
                        new_balance = await adjust_wallet_balance(
                            db=db,
                            user_id=withdrawal.user_id,
                            currency=withdrawal.currency,
                            delta_minor=refund_amount,
                            kind='refund',
                            external_ref_type='withdrawal_failed',
                            external_ref_id=str(withdrawal.id),
                            livemode=livemode
                        )
                        
                        withdrawal.status = 'failed'
                        withdrawal.processed_at = datetime.utcnow()
                        withdrawal.admin_notes = f"Payout failed via webhook: {transfer_id}"
                        
                        await db.commit()
                        logger.info(f"Refunded withdrawal {withdrawal.id}: {refund_amount} {withdrawal.currency} to user {withdrawal.user_id}")
                    except Exception as e:
                        logger.error(f"Failed to refund withdrawal {withdrawal.id}: {str(e)}")
                        await db.rollback()
                        raise
                else:
                    logger.info(f"Withdrawal {withdrawal.id} already marked as failed")
            else:
                logger.warning(f"Withdrawal request not found for failed payout/transfer {transfer_id}")
        
        elif event_type == "payment_intent.succeeded":
            # Handle successful payment intents for wallet top-ups and product purchases
            pi = event['data']['object']
            payment_intent_id = pi['id']
            amount_minor = pi['amount']
            currency = pi['currency']
            metadata = pi.get('metadata', {})
            account_id_str = metadata.get('account_id')
            topup_type = metadata.get('topup_type')
            product_id = metadata.get('product_id', '')
            
            if not account_id_str:
                logger.warning(f"PaymentIntent {payment_intent_id} missing account_id in metadata, skipping")
                # Still record event for idempotency
                transaction = WalletTransaction(
                    user_id=0,
                    amount_minor=0,
                    currency=currency,
                    kind='webhook_event',
                    external_ref_type='stripe_payment_intent',
                    external_ref_id=payment_intent_id,
                    event_id=event_id,
                    livemode=livemode,
                    created_at=datetime.utcnow()
                )
                db.add(transaction)
                await db.commit()
                return {"received": True, "status": "skipped_no_account_id"}
            
            try:
                account_id = int(account_id_str)
            except (ValueError, TypeError):
                logger.warning(f"Invalid account_id in PaymentIntent {payment_intent_id}: {account_id_str}")
                return {"received": True, "status": "skipped_invalid_account_id"}
            
            # Find user by account_id
            user_stmt = select(User).where(User.account_id == account_id)
            user_result = await db.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            
            if not user:
                logger.warning(f"User {account_id} not found for PaymentIntent {payment_intent_id}, skipping")
                # Still record event for idempotency
                transaction = WalletTransaction(
                    user_id=0,
                    amount_minor=0,
                    currency=currency,
                    kind='webhook_event',
                    external_ref_type='stripe_payment_intent',
                    external_ref_id=payment_intent_id,
                    event_id=event_id,
                    livemode=livemode,
                    created_at=datetime.utcnow()
                )
                db.add(transaction)
                await db.commit()
                return {"received": True, "status": "skipped_user_not_found"}
            
            # Check idempotency by payment_intent_id to prevent duplicate credits
            # (event_id is already checked at top level)
            pi_stmt = select(WalletTransaction).where(
                WalletTransaction.external_ref_type == "stripe_payment_intent",
                WalletTransaction.external_ref_id == payment_intent_id
            )
            pi_result = await db.execute(pi_stmt)
            existing_pi = pi_result.scalar_one_or_none()
            
            if existing_pi:
                logger.info(f"PaymentIntent {payment_intent_id} already processed, skipping")
                # Still record webhook event for idempotency (event_id check at top level will catch future retries)
                transaction = WalletTransaction(
                    user_id=0,
                    amount_minor=0,
                    currency=currency,
                    kind='webhook_event',
                    external_ref_type='stripe_webhook',
                    external_ref_id=event_id,
                    event_id=event_id,
                    livemode=livemode,
                    created_at=datetime.utcnow()
                )
                db.add(transaction)
                await db.commit()
                return {"status": "already_processed", "event_id": event_id, "payment_intent_id": payment_intent_id}
            
            # Determine transaction kind based on topup_type
            if topup_type == "wallet_topup":
                kind = "deposit"
            elif topup_type == "product":
                kind = "product_purchase_credit"
            else:
                # Default to deposit if topup_type is missing or unknown
                kind = "deposit"
                logger.warning(f"Unknown topup_type '{topup_type}' for PaymentIntent {payment_intent_id}, using 'deposit'")
            
            # Credit wallet
            try:
                new_balance = await adjust_wallet_balance(
                    db=db,
                    user_id=user.account_id,
                    currency=currency,
                    delta_minor=amount_minor,
                    kind=kind,
                    external_ref_type="stripe_payment_intent",
                    external_ref_id=payment_intent_id,
                    event_id=payment_intent_id,
                    livemode=livemode
                )
                
                logger.info(
                    f"Credited wallet for PaymentIntent {payment_intent_id}: "
                    f"user={user.account_id}, amount={amount_minor} {currency}, "
                    f"new_balance={new_balance}, kind={kind}, product_id={product_id or 'N/A'}"
                )
                
                await db.commit()
                
                return {
                    "received": True,
                    "status": "processed",
                    "event_id": event_id,
                    "payment_intent_id": payment_intent_id,
                    "user_id": user.account_id,
                    "amount_minor": amount_minor,
                    "new_balance_minor": new_balance
                }
                
            except ValueError as e:
                logger.error(f"Failed to adjust wallet balance for PaymentIntent {payment_intent_id}: {str(e)}")
                await db.rollback()
                # Still record event to prevent retry loops
                transaction = WalletTransaction(
                    user_id=user.account_id,
                    amount_minor=0,
                    currency=currency,
                    kind='webhook_event',
                    external_ref_type='stripe_payment_intent',
                    external_ref_id=payment_intent_id,
                    event_id=event_id,
                    livemode=livemode,
                    created_at=datetime.utcnow()
                )
                db.add(transaction)
                await db.commit()
                return {"received": True, "status": "error", "error": str(e)}
            
        elif event_type == "payment_intent.amount_capturable_updated":
            # Handle partial captures
            pi = event['data']['object']
            payment_intent_id = pi['id']
            amount_capturable = pi.get('amount_capturable', 0)
            amount_received = pi.get('amount_received', 0)
            
            logger.info(f"PaymentIntent {payment_intent_id} amount capturable updated: capturable={amount_capturable}, received={amount_received}")
            # For now, just log - implement partial capture logic if needed
        
        elif event_type == "customer.subscription.trial_will_end":
            # Handle trial ending notification
            subscription = event['data']['object']
            subscription_id = subscription.get('id')
            customer_id = subscription.get('customer')
            trial_end = subscription.get('trial_end')
            
            logger.info(f"Subscription {subscription_id} trial ending for customer {customer_id} at {trial_end}")
            # Notify user or update subscription status if needed
        
        elif event_type in ["customer.subscription.created", "customer.subscription.updated", 
                              "customer.subscription.deleted", "customer.subscription.paused",
                              "customer.subscription.resumed"]:
            # Handle subscription lifecycle events
            subscription = event['data']['object']
            subscription_id = subscription.get('id')
            customer_id = subscription.get('customer')
            status = subscription.get('status')
            
            logger.info(f"Subscription {subscription_id} {event_type}: status={status}, customer={customer_id}")
            # Update user_subscriptions table if needed
            
        else:
            logger.info(f"Unhandled event type: {event_type}")
        
        # Record the event in wallet_transactions for idempotency
        # This ensures we don't process the same event twice
        transaction = WalletTransaction(
            user_id=0,  # System event, no specific user
            amount_minor=0,
            currency='usd',
            kind='webhook_event',
            external_ref_type='stripe_webhook',
            external_ref_id=event_id,
            event_id=event_id,
            livemode=livemode,
            created_at=datetime.utcnow()
        )
        db.add(transaction)
        await db.commit()
        
        return {"status": "processed", "event_id": event_id, "event_type": event_type}
        
    except Exception as e:
        logger.error(f"Error processing webhook event {event_id}: {str(e)}", exc_info=True)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing webhook: {str(e)}"
        )

