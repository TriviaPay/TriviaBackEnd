from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import logging
import time

from db import get_db
from models import User, E2EEDevice, E2EEKeyBundle, E2EEOneTimePrekey, DMMessage, DMDelivery
from routers.dependencies import get_current_user
from config import E2EE_DM_ENABLED, E2EE_DM_METRICS_CACHE_SECONDS
from utils.redis_pubsub import get_redis
from routers.dm_sse import _active_dm_sse_connections

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dm", tags=["DM Metrics"])

_metrics_cache = {"ts": 0.0, "payload": None}


def _get_cached_metrics(now_ts: float) -> Optional[Dict[str, Any]]:
    if E2EE_DM_METRICS_CACHE_SECONDS <= 0:
        return None
    payload = _metrics_cache.get("payload")
    if payload and (now_ts - _metrics_cache.get("ts", 0)) < E2EE_DM_METRICS_CACHE_SECONDS:
        return payload
    return None


@router.get("/metrics")
def get_dm_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get comprehensive E2EE DM metrics (admin-only endpoint).
    
    Returns:
    - SSE connection stats
    - Redis status and lag
    - OTPK pool distribution
    - Message delivery stats
    - Device and bundle stats
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")
    
    # Only allow admins to access metrics
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    now_ts = time.time()
    cached = _get_cached_metrics(now_ts)
    if cached:
        return cached
    
    now = datetime.utcnow()
    
    # Count active SSE connections
    total_sse_connections = sum(len(sessions) for sessions in _active_dm_sse_connections.values())
    sse_connections_per_user = {
        str(user_id): len(sessions) 
        for user_id, sessions in _active_dm_sse_connections.items()
    }
    
    # Redis status
    redis_client = get_redis()
    redis_status = "available" if redis_client else "unavailable"
    redis_lag_ms = 0  # Would need to track actual lag, placeholder for now
    
    # OTPK pool distribution
    otpk_stats = db.query(
        E2EEOneTimePrekey.device_id,
        func.count(E2EEOneTimePrekey.id).filter(E2EEOneTimePrekey.claimed == False).label('available'),
        func.count(E2EEOneTimePrekey.id).filter(E2EEOneTimePrekey.claimed == True).label('claimed')
    ).group_by(E2EEOneTimePrekey.device_id).all()
    
    devices_low_otpk = []
    devices_critical_otpk = []
    total_available_otpks = 0
    total_claimed_otpks = 0
    
    for device_id, available, claimed in otpk_stats:
        total_available_otpks += available or 0
        total_claimed_otpks += claimed or 0
        
        if available is not None:
            if available < 2:  # Critical watermark
                devices_critical_otpk.append(str(device_id))
            elif available < 5:  # Low watermark
                devices_low_otpk.append(str(device_id))
    
    # Signed prekey age tracking
    from config import E2EE_DM_SIGNED_PREKEY_MAX_AGE_DAYS
    old_prekey_cutoff = now - timedelta(days=E2EE_DM_SIGNED_PREKEY_MAX_AGE_DAYS)
    old_prekeys = db.query(E2EEKeyBundle).filter(
        E2EEKeyBundle.updated_at < old_prekey_cutoff
    ).count()
    
    # Message stats
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    message_counts = db.query(
        func.count(DMMessage.id).filter(DMMessage.created_at >= today_start).label("today"),
        func.count(DMMessage.id).filter(DMMessage.created_at >= now - timedelta(hours=1)).label("last_hour")
    ).one()
    
    # Delivery stats
    delivery_counts = db.query(
        func.count(DMDelivery.id).filter(DMDelivery.delivered_at.is_(None)).label("undelivered"),
        func.count(DMDelivery.id).filter(
            DMDelivery.read_at.is_(None),
            DMDelivery.delivered_at.isnot(None)
        ).label("unread")
    ).one()
    
    # Calculate delivery latency (p95/p99 would require more complex query)
    # For now, calculate average delivery time for messages delivered in last hour
    recent_deliveries = db.query(
        func.avg(
            func.extract('epoch', DMDelivery.delivered_at - DMMessage.created_at) * 1000
        ).label('avg_delivery_ms')
    ).join(
        DMMessage, DMDelivery.message_id == DMMessage.id
    ).filter(
        DMDelivery.delivered_at >= now - timedelta(hours=1),
        DMDelivery.delivered_at.isnot(None)
    ).scalar()
    
    avg_delivery_ms = float(recent_deliveries) if recent_deliveries else 0
    
    # Device stats
    device_counts = db.query(
        func.count(E2EEDevice.device_id).label("total"),
        func.count(E2EEDevice.device_id).filter(E2EEDevice.status == "active").label("active"),
        func.count(E2EEDevice.device_id).filter(E2EEDevice.status == "revoked").label("revoked")
    ).one()
    
    payload = {
        "status": "success",
        "timestamp": now.isoformat(),
        "metrics": {
            "sse_connections": {
                "total": total_sse_connections,
                "per_user": sse_connections_per_user,
                "max_per_user": 3  # From config
            },
            "redis": {
                "status": redis_status,
                "lag_ms": redis_lag_ms,
                "available": redis_status == "available"
            },
            "otpk_pools": {
                "total_available": total_available_otpks,
                "total_claimed": total_claimed_otpks,
                "devices_low_watermark": len(devices_low_otpk),
                "devices_critical_watermark": len(devices_critical_otpk),
                "device_ids_low": devices_low_otpk[:10],  # Limit to first 10
                "device_ids_critical": devices_critical_otpk[:10]
            },
            "signed_prekeys": {
                "old_prekeys_count": old_prekeys,
                "max_age_days": E2EE_DM_SIGNED_PREKEY_MAX_AGE_DAYS
            },
            "messages": {
                "today": message_counts.today,
                "last_hour": message_counts.last_hour
            },
            "delivery": {
                "undelivered": delivery_counts.undelivered,
                "unread": delivery_counts.unread,
                "avg_delivery_ms": round(avg_delivery_ms, 2)
            },
            "devices": {
                "total": device_counts.total,
                "active": device_counts.active,
                "revoked": device_counts.revoked
            }
        }
    }
    
    _metrics_cache["ts"] = now_ts
    _metrics_cache["payload"] = payload
    
    return payload
