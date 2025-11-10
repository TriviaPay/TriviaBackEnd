"""
Stripe Metrics Collection

Basic metrics collection for Stripe operations. Can be extended to export
to Prometheus, DataDog, or other metrics systems.
"""
import logging
from typing import Dict, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func
from models import StripeWebhookEvent, WalletLedger, PaymentTransaction, WithdrawalRequest

logger = logging.getLogger(__name__)

# Simple in-memory counters (in production, use a proper metrics system)
_metrics_cache = {
    'webhook_events_processed': {},
    'wallet_deposits_total': {},
    'wallet_withdrawals_total': {},
    'failed_withdrawals_total': 0,
    'instant_withdraw_fee_minor_total': 0
}


def get_webhook_metrics(db: Session, hours: int = 24) -> Dict:
    """
    Get webhook processing metrics.
    
    Args:
        db: Database session
        hours: Number of hours to look back
        
    Returns:
        Dict with metrics by event type and status
    """
    since = datetime.utcnow() - timedelta(hours=hours)
    
    # Count by type and status
    events = db.query(
        StripeWebhookEvent.type,
        StripeWebhookEvent.status,
        func.count(StripeWebhookEvent.event_id).label('count')
    ).filter(
        StripeWebhookEvent.received_at >= since
    ).group_by(
        StripeWebhookEvent.type,
        StripeWebhookEvent.status
    ).all()
    
    metrics = {}
    for event_type, status, count in events:
        if event_type not in metrics:
            metrics[event_type] = {}
        metrics[event_type][status] = count
    
    return metrics


def get_wallet_metrics(db: Session, currency: str = 'usd', hours: int = 24) -> Dict:
    """
    Get wallet operation metrics.
    
    Args:
        db: Database session
        currency: Currency to filter by
        hours: Number of hours to look back
        
    Returns:
        Dict with deposit/withdrawal totals
    """
    since = datetime.utcnow() - timedelta(hours=hours)
    
    # Total deposits
    deposits = db.query(func.sum(WalletLedger.delta_minor)).filter(
        WalletLedger.currency == currency,
        WalletLedger.kind == 'deposit',
        WalletLedger.created_at >= since
    ).scalar() or 0
    
    # Total withdrawals
    withdrawals = db.query(func.sum(func.abs(WalletLedger.delta_minor))).filter(
        WalletLedger.currency == currency,
        WalletLedger.kind == 'withdraw',
        WalletLedger.created_at >= since
    ).scalar() or 0
    
    # Total fees
    fees = db.query(func.sum(func.abs(WalletLedger.delta_minor))).filter(
        WalletLedger.currency == currency,
        WalletLedger.kind == 'fee',
        WalletLedger.created_at >= since
    ).scalar() or 0
    
    return {
        'deposits_total_minor': deposits,
        'withdrawals_total_minor': withdrawals,
        'fees_total_minor': fees,
        'currency': currency,
        'period_hours': hours
    }


def get_withdrawal_metrics(db: Session, hours: int = 24) -> Dict:
    """
    Get withdrawal metrics.
    
    Args:
        db: Database session
        hours: Number of hours to look back
        
    Returns:
        Dict with withdrawal statistics
    """
    since = datetime.utcnow() - timedelta(hours=hours)
    
    # Failed withdrawals
    failed_count = db.query(func.count(WithdrawalRequest.id)).filter(
        WithdrawalRequest.status == 'failed',
        WithdrawalRequest.requested_at >= since
    ).scalar() or 0
    
    # Instant withdrawal fees
    instant_fees = db.query(func.sum(WithdrawalRequest.fee_minor)).filter(
        WithdrawalRequest.method == 'instant',
        WithdrawalRequest.status.in_(['processing', 'paid']),
        WithdrawalRequest.requested_at >= since
    ).scalar() or 0
    
    return {
        'failed_withdrawals_total': failed_count,
        'instant_withdraw_fee_minor_total': instant_fees,
        'period_hours': hours
    }


def get_reconciliation_drift(db: Session, currency: str = 'usd') -> Optional[Dict]:
    """
    Get reconciliation drift for a currency.
    
    Args:
        db: Database session
        currency: Currency to check
        
    Returns:
        Dict with drift information or None if no snapshot exists
    """
    from models import StripeReconciliationSnapshot
    
    # Get latest snapshot
    snapshot = db.query(StripeReconciliationSnapshot).filter(
        StripeReconciliationSnapshot.currency == currency
    ).order_by(StripeReconciliationSnapshot.as_of_date.desc()).first()
    
    if not snapshot:
        return None
    
    # Calculate drift (this would compare with Stripe balance in production)
    return {
        'currency': currency,
        'as_of_date': snapshot.as_of_date.isoformat(),
        'platform_available_minor': snapshot.platform_available_minor,
        'platform_pending_minor': snapshot.platform_pending_minor,
        'drift_available_minor': 0,  # Would be calculated vs Stripe
        'drift_pending_minor': 0  # Would be calculated vs Stripe
    }

