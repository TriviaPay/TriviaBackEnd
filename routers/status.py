from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import desc, and_, or_
from datetime import datetime, timedelta
from typing import Optional, List
import uuid
import logging

from db import get_db
from models import User, StatusPost, StatusAudience, StatusView, DMParticipant, DMConversation, Block
from routers.dependencies import get_current_user
from config import STATUS_ENABLED, STATUS_TTL_HOURS, STATUS_MAX_POSTS_PER_DAY, STATUS_ATTACHMENT_MAX_MB
from utils.redis_pubsub import publish_dm_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/status", tags=["Status"])


class CreateStatusPostRequest(BaseModel):
    media_meta: dict = Field(..., description="Encrypted media metadata (JSON)", example={"url": "https://example.com/media.jpg", "size": 1024000, "mime": "image/jpeg"})
    audience_mode: str = Field(..., pattern="^(contacts|custom)$", example="contacts")
    custom_audience: Optional[List[int]] = Field(None, description="Custom user IDs if audience_mode='custom'", example=[1142961859, 9876543210])
    
    class Config:
        json_schema_extra = {
            "example": {
                "media_meta": {
                    "url": "https://example.com/media.jpg",
                    "size": 1024000,
                    "mime": "image/jpeg",
                    "sha256": "abc123def456..."
                },
                "audience_mode": "contacts",
                "custom_audience": [1142961859, 9876543210]
            }
        }


class MarkViewedRequest(BaseModel):
    post_ids: List[str] = Field(..., min_items=1, example=["550e8400-e29b-41d4-a716-446655440000", "660e8400-e29b-41d4-a716-446655440001"])
    
    class Config:
        json_schema_extra = {
            "example": {
                "post_ids": ["550e8400-e29b-41d4-a716-446655440000", "660e8400-e29b-41d4-a716-446655440001"]
            }
        }


def get_user_contacts(db: Session, user_id: int) -> List[int]:
    """Get list of user IDs the user has DM conversations with."""
    conversation_ids = db.query(DMParticipant.conversation_id).filter(
        DMParticipant.user_id == user_id
    ).subquery()

    contacts = db.query(DMParticipant.user_id).filter(
        DMParticipant.conversation_id.in_(conversation_ids),
        DMParticipant.user_id != user_id
    ).distinct().all()

    return [row[0] for row in contacts]


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
    
    db.query(User).filter(User.account_id == current_user.account_id).with_for_update().first()

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
    audience_records = [
        StatusAudience(post_id=new_post.id, viewer_user_id=viewer_id)
        for viewer_id in audience_user_ids
    ]
    if audience_records:
        db.bulk_save_objects(audience_records)
    
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
    limit: int = Query(default=20, ge=1, le=50, example=20),
    cursor: Optional[str] = Query(None, description="Post ID cursor for pagination", example="550e8400-e29b-41d4-a716-446655440000"),
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
    parsed_ids = []
    for post_id_str in request.post_ids:
        try:
            parsed_ids.append(uuid.UUID(post_id_str))
        except ValueError:
            continue

    if not parsed_ids:
        return {"viewed_post_ids": viewed_posts}

    audience_rows = db.query(StatusAudience.post_id).filter(
        StatusAudience.post_id.in_(parsed_ids),
        StatusAudience.viewer_user_id == current_user.account_id
    ).all()
    allowed_ids = {row[0] for row in audience_rows}
    if not allowed_ids:
        return {"viewed_post_ids": viewed_posts}

    existing_views = db.query(StatusView.post_id).filter(
        StatusView.post_id.in_(list(allowed_ids)),
        StatusView.viewer_user_id == current_user.account_id
    ).all()
    existing_ids = {row[0] for row in existing_views}

    new_ids = [post_id for post_id in parsed_ids if post_id in allowed_ids and post_id not in existing_ids]
    if new_ids:
        now = datetime.utcnow()
        rows = [
            {"post_id": post_id, "viewer_user_id": current_user.account_id, "viewed_at": now}
            for post_id in new_ids
        ]
        dialect_name = db.bind.dialect.name if db.bind else ""
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            stmt = pg_insert(StatusView).values(rows)
            stmt = stmt.on_conflict_do_nothing(index_elements=["post_id", "viewer_user_id"])
            db.execute(stmt)
        elif dialect_name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            stmt = sqlite_insert(StatusView).values(rows).prefix_with("OR IGNORE")
            db.execute(stmt)
        else:
            db.bulk_save_objects([
                StatusView(
                    post_id=post_id,
                    viewer_user_id=current_user.account_id,
                    viewed_at=now
                )
                for post_id in new_ids
            ])
        viewed_posts = [str(post_id) for post_id in new_ids]
    
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
    user_ids: List[int] = Query(..., description="User IDs to check presence for", example=[1142961859, 9876543210]),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get presence for user_ids. Respects privacy settings.
    """
    if not STATUS_ENABLED:
        raise HTTPException(status_code=403, detail="Status feature is not enabled")
    
    from models import UserPresence

    query_user_ids = list(dict.fromkeys(user_ids))
    presences = []
    if query_user_ids:
        presences = db.query(UserPresence).filter(UserPresence.user_id.in_(query_user_ids)).all()
    presence_map = {p.user_id: p for p in presences}
    contact_ids = set(get_user_contacts(db, current_user.account_id))

    blocked_ids = set()
    if query_user_ids:
        blocks = db.query(Block.blocker_id, Block.blocked_id).filter(
            or_(Block.blocker_id == current_user.account_id, Block.blocked_id == current_user.account_id),
            or_(Block.blocker_id.in_(query_user_ids), Block.blocked_id.in_(query_user_ids))
        ).all()
        for blocker_id, blocked_id in blocks:
            if blocker_id == current_user.account_id:
                blocked_ids.add(blocked_id)
            else:
                blocked_ids.add(blocker_id)

    result = []
    for user_id in user_ids:
        if user_id in blocked_ids and user_id != current_user.account_id:
            result.append({
                "user_id": user_id,
                "last_seen_at": None,
                "device_online": False
            })
            continue

        presence = presence_map.get(user_id)
        if user_id == current_user.account_id:
            last_seen = presence.last_seen_at.isoformat() if presence and presence.last_seen_at else None
            device_online = presence.device_online if presence else False
        else:
            privacy = presence.privacy_settings if presence and presence.privacy_settings else {}
            share_online = privacy.get("share_online", True)
            share_last_seen = privacy.get("share_last_seen", "contacts")
            if share_last_seen == "all":
                share_last_seen = "everyone"
            is_contact = user_id in contact_ids
            device_online = presence.device_online if presence and share_online else False
            if presence and presence.last_seen_at and (share_last_seen == "everyone" or (share_last_seen == "contacts" and is_contact)):
                last_seen = presence.last_seen_at.isoformat()
            else:
                last_seen = None

        result.append({
            "user_id": user_id,
            "last_seen_at": last_seen,
            "device_online": device_online
        })
    
    return {"presence": result}
