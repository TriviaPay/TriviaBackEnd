from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import GroupSendMessageRequest
from .service import (
    delete_group_message as service_delete_group_message,
    list_group_messages as service_list_group_messages,
    mark_group_message_delivered as service_mark_group_message_delivered,
    mark_group_message_read as service_mark_group_message_read,
    send_group_message as service_send_group_message,
)

router = APIRouter(prefix="/groups", tags=["Group Messages"])


@router.get("/{group_id}/messages")
async def get_messages(
    group_id: str,
    limit: int = Query(default=50, ge=1, le=100, example=50),
    before: Optional[str] = Query(None, description="Message ID to fetch before"),
    after: Optional[str] = Query(None, description="Message ID to fetch after"),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Get group message history (ciphertext envelopes)."""
    return await service_list_group_messages(
        db,
        current_user=current_user,
        group_id=group_id,
        limit=limit,
        before=before,
        after=after,
    )


@router.post("/{group_id}/messages")
async def send_message(
    group_id: str,
    request: GroupSendMessageRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Send an encrypted message to a group."""
    return await service_send_group_message(
        db, current_user=current_user, group_id=group_id, request=request
    )


@router.post("/group-messages/{message_id}/delivered")
async def mark_delivered(
    message_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Mark message as delivered. Idempotent."""
    return service_mark_group_message_delivered(
        db, current_user=current_user, message_id=message_id
    )


@router.post("/group-messages/{message_id}/read")
async def mark_read(
    message_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Mark message as read. Idempotent."""
    return service_mark_group_message_read(
        db, current_user=current_user, message_id=message_id
    )


@router.delete("/group-messages/{message_id}")
async def delete_message(
    message_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Soft delete message. Sender or admin only."""
    return service_delete_group_message(
        db, current_user=current_user, message_id=message_id
    )
