from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import logging
import time

from db import get_db
from models import User, Group, GroupParticipant, GroupMessage, GroupSenderKey
from routers.dependencies import get_current_user
from config import GROUPS_ENABLED, GROUP_METRICS_CACHE_SECONDS
from utils.redis_pubsub import get_redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/groups", tags=["Group Metrics"])

_metrics_cache = {"ts": 0.0, "payload": None}


def _get_cached_metrics(now_ts: float) -> Optional[Dict[str, Any]]:
    if GROUP_METRICS_CACHE_SECONDS <= 0:
        return None
    payload = _metrics_cache.get("payload")
    if payload and (now_ts - _metrics_cache.get("ts", 0)) < GROUP_METRICS_CACHE_SECONDS:
        return payload
    return None


@router.get("/metrics")
def get_group_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get comprehensive group metrics (admin-only).
    """
    if not GROUPS_ENABLED:
        raise HTTPException(status_code=403, detail="Groups feature is not enabled")
    
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    now_ts = time.time()
    cached = _get_cached_metrics(now_ts)
    if cached:
        return cached
    
    now = datetime.utcnow()
    
    # Total groups (one query)
    group_counts = db.query(
        func.count(Group.id).label("total"),
        func.count(Group.id).filter(Group.is_closed.is_(False)).label("active")
    ).one()
    total_groups = group_counts.total or 0
    active_groups = group_counts.active or 0
    
    # Average group size (subquery to avoid aggregate-of-aggregate)
    participant_counts_subq = db.query(
        GroupParticipant.group_id.label("group_id"),
        func.count(GroupParticipant.user_id).label("participant_count")
    ).filter(
        GroupParticipant.is_banned.is_(False)
    ).group_by(GroupParticipant.group_id).subquery()
    
    avg_size = db.query(func.avg(participant_counts_subq.c.participant_count)).scalar() or 0
    
    # Message stats (one query)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    message_counts = db.query(
        func.count(GroupMessage.id).filter(GroupMessage.created_at >= today_start).label("today"),
        func.count(GroupMessage.id).filter(GroupMessage.created_at >= now - timedelta(hours=1)).label("last_hour")
    ).one()
    messages_today = message_counts.today or 0
    messages_last_hour = message_counts.last_hour or 0
    
    # Sender key distribution count
    sender_key_count = db.query(GroupSenderKey).count()
    
    # Rekey frequency (epoch changes in last 24h)
    yesterday = datetime.utcnow() - timedelta(days=1)
    groups_with_epoch_changes = db.query(Group).filter(
        Group.updated_at >= yesterday
    ).count()
    
    # Redis status
    redis_client = get_redis()
    redis_status = "available" if redis_client else "unavailable"
    
    payload = {
        "status": "success",
        "timestamp": now.isoformat(),
        "metrics": {
            "groups": {
                "total": total_groups,
                "active": active_groups,
                "closed": total_groups - active_groups
            },
            "participants": {
                "average_per_group": round(float(avg_size), 2)
            },
            "messages": {
                "today": messages_today,
                "last_hour": messages_last_hour
            },
            "sender_keys": {
                "total_distributions": sender_key_count
            },
            "rekey": {
                "groups_with_epoch_changes_24h": groups_with_epoch_changes
            },
            "redis": {
                "status": redis_status
            }
        }
    }
    _metrics_cache["ts"] = now_ts
    _metrics_cache["payload"] = payload
    return payload
