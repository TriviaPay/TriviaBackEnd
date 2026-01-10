from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from db import get_db
from models import User
from routers.dependencies import get_current_user
from utils.redis_pubsub import publish_dm_message

from .schemas import CreateStatusPostRequest, MarkViewedRequest
from .service import (
    create_status_post as service_create_status_post,
    delete_status_post as service_delete_status_post,
    get_status_feed as service_get_status_feed,
    get_status_presence as service_get_status_presence,
    mark_status_viewed as service_mark_status_viewed,
)

router = APIRouter(prefix="/status", tags=["Status"])


@router.post("/posts")
async def create_status_post(
    request: CreateStatusPostRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a status post (24h ephemeral).
    Server expands audience and fan-outs post-key notices.
    """
    response = service_create_status_post(db, current_user=current_user, request=request)

    for viewer_id in response.pop("audience_user_ids", []):
        event = {
            "type": "status_post",
            "post_id": response["id"],
            "owner_user_id": current_user.account_id,
            "created_at": response["created_at"],
            "expires_at": response["expires_at"],
        }
        await publish_dm_message("", viewer_id, event)

    return response


@router.get("/feed")
async def get_status_feed(
    limit: int = Query(default=20, ge=1, le=50, example=20),
    cursor: Optional[str] = Query(
        None,
        description="Post ID cursor for pagination",
        example="550e8400-e29b-41d4-a716-446655440000",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get available status posts for the user (posts where user is in audience).
    Returns ciphertext descriptors.
    """
    return service_get_status_feed(
        db, current_user=current_user, limit=limit, cursor=cursor
    )


@router.post("/views")
async def mark_viewed(
    request: MarkViewedRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Mark status posts as viewed. Respects privacy settings.
    """
    return service_mark_status_viewed(db, current_user=current_user, request=request)


@router.delete("/posts/{post_id}")
async def delete_status_post(
    post_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete my status post."""
    return service_delete_status_post(db, current_user=current_user, post_id=post_id)


@router.get("/presence")
async def get_presence(
    user_ids: List[int] = Query(
        ...,
        description="User IDs to check presence for",
        example=[1142961859, 9876543210],
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get presence for user_ids. Respects privacy settings.
    """
    return service_get_status_presence(
        db, current_user=current_user, user_ids=user_ids
    )
