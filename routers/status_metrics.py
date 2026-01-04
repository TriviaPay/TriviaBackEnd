from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, case
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
    
    # Posts today / active / expired in a single pass
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    now = datetime.utcnow()
    post_counts = db.query(
        func.sum(case((StatusPost.created_at >= today_start, 1), else_=0)),
        func.sum(case((StatusPost.expires_at > now, 1), else_=0)),
        func.sum(case((StatusPost.expires_at <= now, 1), else_=0))
    ).first()
    posts_today = post_counts[0] if post_counts and post_counts[0] is not None else 0
    active_posts = post_counts[1] if post_counts and post_counts[1] is not None else 0
    expired_posts = post_counts[2] if post_counts and post_counts[2] is not None else 0

    # Views today
    views_today = db.query(func.count(StatusView.post_id)).filter(
        StatusView.viewed_at >= today_start
    ).scalar() or 0

    # Average audience size
    audience_counts = db.query(
        StatusAudience.post_id.label("post_id"),
        func.count(StatusAudience.viewer_user_id).label("viewer_count")
    ).group_by(StatusAudience.post_id).subquery()
    avg_audience = db.query(func.avg(audience_counts.c.viewer_count)).scalar() or 0
    
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
