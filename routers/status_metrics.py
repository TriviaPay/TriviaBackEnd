from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta
import logging

from db import get_db
from models import User, StatusPost, StatusView, StatusAudience
from routers.dependencies import get_current_user
from config import STATUS_ENABLED

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/status", tags=["Status Metrics"])


@router.get("/metrics")
async def get_status_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get comprehensive status metrics (admin-only).
    """
    if not STATUS_ENABLED:
        raise HTTPException(status_code=403, detail="Status feature is not enabled")
    
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Posts today
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    posts_today = db.query(StatusPost).filter(
        StatusPost.created_at >= today_start
    ).count()
    
    # Total active posts (not expired)
    now = datetime.utcnow()
    active_posts = db.query(StatusPost).filter(
        StatusPost.expires_at > now
    ).count()
    
    # Views today
    views_today = db.query(StatusView).filter(
        StatusView.viewed_at >= today_start
    ).count()
    
    # Average audience size
    avg_audience = db.query(func.avg(
        func.count(StatusAudience.viewer_user_id)
    )).select_from(StatusAudience).group_by(
        StatusAudience.post_id
    ).scalar() or 0
    
    # Expired posts (for cleanup)
    expired_posts = db.query(StatusPost).filter(
        StatusPost.expires_at <= now
    ).count()
    
    return {
        "status": "success",
        "timestamp": datetime.utcnow().isoformat(),
        "metrics": {
            "posts": {
                "today": posts_today,
                "active": active_posts,
                "expired": expired_posts
            },
            "views": {
                "today": views_today
            },
            "audience": {
                "average_size": round(float(avg_audience), 2)
            }
        }
    }

