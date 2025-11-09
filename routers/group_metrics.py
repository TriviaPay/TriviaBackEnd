from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
import logging

from db import get_db
from models import User, Group, GroupParticipant, GroupMessage, GroupSenderKey
from routers.dependencies import get_current_user
from config import GROUPS_ENABLED
from utils.redis_pubsub import get_redis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/groups", tags=["Group Metrics"])


@router.get("/metrics")
async def get_group_metrics(
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
    
    # Total groups
    total_groups = db.query(Group).count()
    active_groups = db.query(Group).filter(Group.is_closed == False).count()
    
    # Average group size
    avg_size = db.query(func.avg(
        func.count(GroupParticipant.user_id)
    )).select_from(GroupParticipant).filter(
        GroupParticipant.is_banned == False
    ).group_by(GroupParticipant.group_id).scalar() or 0
    
    # Message stats
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    messages_today = db.query(GroupMessage).filter(
        GroupMessage.created_at >= today_start
    ).count()
    
    messages_last_hour = db.query(GroupMessage).filter(
        GroupMessage.created_at >= datetime.utcnow() - timedelta(hours=1)
    ).count()
    
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
    
    return {
        "status": "success",
        "timestamp": datetime.utcnow().isoformat(),
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

