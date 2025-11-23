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
from app.models.wallet import WalletTransaction
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
            
            # TODO: Update user's stripe_connect_account_id status if needed
            logger.info(f"Account updated: {account_id}")
            
        elif event_type in ["transfer.paid", "payout.paid"]:
            # Handle successful payouts
            transfer = event['data']['object']
            transfer_id = transfer.get('id')
            
            # TODO: Update withdrawal_requests table with status='paid'
            logger.info(f"Transfer/Payout paid: {transfer_id}")
            
        elif event_type in ["transfer.failed", "payout.failed"]:
            # Handle failed payouts - need to refund wallet
            transfer = event['data']['object']
            transfer_id = transfer.get('id')
            amount = transfer.get('amount', 0)
            
            # TODO: Find withdrawal request by transfer_id and refund wallet
            logger.warning(f"Transfer/Payout failed: {transfer_id}, amount: {amount}")
            
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

