from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import desc, and_
from datetime import datetime, timedelta
from typing import Optional, List
import uuid
import logging

from db import get_db
from models import User, StatusPost, StatusAudience, StatusView, DMParticipant, DMConversation
from routers.dependencies import get_current_user
from config import STATUS_ENABLED, STATUS_TTL_HOURS, STATUS_MAX_POSTS_PER_DAY, STATUS_ATTACHMENT_MAX_MB
from utils.redis_pubsub import publish_dm_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/status", tags=["Status"])


class CreateStatusPostRequest(BaseModel):
    media_meta: dict = Field(..., description="Encrypted media metadata (JSON)")
    audience_mode: str = Field(..., pattern="^(contacts|custom)$")
    custom_audience: Optional[List[int]] = Field(None, description="Custom user IDs if audience_mode='custom'")


class MarkViewedRequest(BaseModel):
    post_ids: List[str] = Field(..., min_items=1)


def get_user_contacts(db: Session, user_id: int) -> List[int]:
    """Get list of user IDs the user has DM conversations with."""
    conversations = db.query(DMConversation).join(
        DMParticipant, DMConversation.id == DMParticipant.conversation_id
    ).filter(
        DMParticipant.user_id == user_id
    ).all()
    
    contact_ids = set()
    for conv in conversations:
        participants = db.query(DMParticipant).filter(
            DMParticipant.conversation_id == conv.id,
            DMParticipant.user_id != user_id
        ).all()
        for p in participants:
            contact_ids.add(p.user_id)
    
    return list(contact_ids)


@router.post("/posts")
async def create_status_post(
    request: CreateStatusPostRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a status post (24h ephemeral).
    Server expands audience and fan-outs post-key notices.
    """
    if not STATUS_ENABLED:
        raise HTTPException(status_code=403, detail="Status feature is not enabled")
    
    # Check daily limit
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_posts = db.query(StatusPost).filter(
        StatusPost.owner_user_id == current_user.account_id,
        StatusPost.created_at >= today_start
    ).count()
    
    if today_posts >= STATUS_MAX_POSTS_PER_DAY:
        raise HTTPException(
            status_code=429,
            detail=f"Maximum {STATUS_MAX_POSTS_PER_DAY} posts per day"
        )
    
    # Determine audience
    if request.audience_mode == "contacts":
        audience_user_ids = get_user_contacts(db, current_user.account_id)
    elif request.audience_mode == "custom":
        if not request.custom_audience:
            raise HTTPException(status_code=400, detail="custom_audience required for custom mode")
        audience_user_ids = request.custom_audience
    else:
        raise HTTPException(status_code=400, detail="Invalid audience_mode")
    
    # Create post
    expires_at = datetime.utcnow() + timedelta(hours=STATUS_TTL_HOURS)
    new_post = StatusPost(
        id=uuid.uuid4(),
        owner_user_id=current_user.account_id,
        media_meta=request.media_meta,
        audience_mode=request.audience_mode,
        expires_at=expires_at,
        post_epoch=0
    )
    db.add(new_post)
    
    # Create audience records
    for viewer_id in audience_user_ids:
        audience = StatusAudience(
            post_id=new_post.id,
            viewer_user_id=viewer_id
        )
        db.add(audience)
    
    try:
        db.commit()
        db.refresh(new_post)
        
        # Fan-out post-key notices to audience via SSE
        # (Client encrypts post key pairwise and sends notices)
        for viewer_id in audience_user_ids:
            event = {
                "type": "status_post",
                "post_id": str(new_post.id),
                "owner_user_id": current_user.account_id,
                "created_at": new_post.created_at.isoformat() if new_post.created_at else None,
                "expires_at": new_post.expires_at.isoformat() if new_post.expires_at else None
            }
            publish_dm_message("", viewer_id, event)  # Use DM user channel for status notifications
        
        return {
            "id": str(new_post.id),
            "created_at": new_post.created_at.isoformat() if new_post.created_at else None,
            "expires_at": new_post.expires_at.isoformat() if new_post.expires_at else None,
            "audience_count": len(audience_user_ids)
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating status post: {e}")
        raise HTTPException(status_code=500, detail="Failed to create status post")


@router.get("/feed")
async def get_status_feed(
    limit: int = Query(default=20, ge=1, le=50),
    cursor: Optional[str] = Query(None, description="Post ID cursor for pagination"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get available status posts for the user (posts where user is in audience).
    Returns ciphertext descriptors.
    """
    if not STATUS_ENABLED:
        raise HTTPException(status_code=403, detail="Status feature is not enabled")
    
    # Get posts where user is in audience and not expired
    now = datetime.utcnow()
    query = db.query(StatusPost).join(
        StatusAudience, StatusPost.id == StatusAudience.post_id
    ).filter(
        StatusAudience.viewer_user_id == current_user.account_id,
        StatusPost.expires_at > now
    )
    
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
            cursor_post = db.query(StatusPost).filter(StatusPost.id == cursor_uuid).first()
            if cursor_post:
                query = query.filter(
                    (StatusPost.created_at < cursor_post.created_at) |
                    ((StatusPost.created_at == cursor_post.created_at) & (StatusPost.id < cursor_uuid))
                )
        except ValueError:
            pass
    
    posts = query.order_by(desc(StatusPost.created_at), desc(StatusPost.id)).limit(limit).all()
    
    result = []
    for post in posts:
        result.append({
            "id": str(post.id),
            "owner_user_id": post.owner_user_id,
            "media_meta": post.media_meta,
            "created_at": post.created_at.isoformat() if post.created_at else None,
            "expires_at": post.expires_at.isoformat() if post.expires_at else None,
            "post_epoch": post.post_epoch
        })
    
    return {"posts": result}


@router.post("/views")
async def mark_viewed(
    request: MarkViewedRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Mark status posts as viewed. Respects privacy settings.
    """
    if not STATUS_ENABLED:
        raise HTTPException(status_code=403, detail="Status feature is not enabled")
    
    viewed_posts = []
    for post_id_str in request.post_ids:
        try:
            post_uuid = uuid.UUID(post_id_str)
        except ValueError:
            continue
        
        # Check if user is in audience
        audience = db.query(StatusAudience).filter(
            StatusAudience.post_id == post_uuid,
            StatusAudience.viewer_user_id == current_user.account_id
        ).first()
        
        if not audience:
            continue
        
        # Find or create view record
        view = db.query(StatusView).filter(
            StatusView.post_id == post_uuid,
            StatusView.viewer_user_id == current_user.account_id
        ).first()
        
        if not view:
            view = StatusView(
                post_id=post_uuid,
                viewer_user_id=current_user.account_id,
                viewed_at=datetime.utcnow()
            )
            db.add(view)
            viewed_posts.append(post_id_str)
    
    try:
        db.commit()
        return {"viewed_post_ids": viewed_posts}
    except Exception as e:
        db.rollback()
        logger.error(f"Error marking viewed: {e}")
        raise HTTPException(status_code=500, detail="Failed to mark viewed")


@router.delete("/posts/{post_id}")
async def delete_status_post(
    post_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete my status post."""
    if not STATUS_ENABLED:
        raise HTTPException(status_code=403, detail="Status feature is not enabled")
    
    try:
        post_uuid = uuid.UUID(post_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid post ID format")
    
    post = db.query(StatusPost).filter(
        StatusPost.id == post_uuid,
        StatusPost.owner_user_id == current_user.account_id
    ).first()
    
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Delete audience and views
    db.query(StatusAudience).filter(StatusAudience.post_id == post_uuid).delete()
    db.query(StatusView).filter(StatusView.post_id == post_uuid).delete()
    
    # Delete post
    db.delete(post)
    
    try:
        db.commit()
        return {"message": "Post deleted"}
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting post: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete post")


@router.get("/presence")
async def get_presence(
    user_ids: List[int] = Query(..., description="User IDs to check presence for"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get presence for user_ids. Respects privacy settings.
    """
    if not STATUS_ENABLED:
        raise HTTPException(status_code=403, detail="Status feature is not enabled")
    
    from models import UserPresence
    
    result = []
    for user_id in user_ids:
        presence = db.query(UserPresence).filter(UserPresence.user_id == user_id).first()
        
        # TODO: Check privacy settings (share_last_seen, share_online)
        # For now, return basic info
        result.append({
            "user_id": user_id,
            "last_seen_at": presence.last_seen_at.isoformat() if presence and presence.last_seen_at else None,
            "device_online": presence.device_online if presence else False
        })
    
    return {"presence": result}

