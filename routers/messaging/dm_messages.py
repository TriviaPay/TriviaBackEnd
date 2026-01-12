from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import (
    GetMessagesResponse,
    MarkDeliveredResponse,
    MarkReadResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from .service import get_dm_messages as service_get_dm_messages
from .service import mark_dm_delivered as service_mark_dm_delivered
from .service import mark_dm_read as service_mark_dm_read
from .service import send_dm_message as service_send_dm_message

router = APIRouter(prefix="/dm", tags=["DM Messages"])


@router.post(
    "/conversations/{conversation_id}/messages", response_model=SendMessageResponse
)
async def send_message(  # noqa: D401
    conversation_id: str,
    request: SendMessageRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Send an encrypted message to a conversation.
    Stores ciphertext and publishes to Redis for real-time delivery.
    """
    return service_send_dm_message(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        request=request,
        background_tasks=background_tasks,
    )


@router.get(
    "/conversations/{conversation_id}/messages", response_model=GetMessagesResponse
)
async def get_messages(  # noqa: D401
    conversation_id: str,
    limit: int = Query(50, ge=1, le=100, example=50),
    since: Optional[str] = Query(
        None,
        description="Message ID to fetch messages after",
        example="550e8400-e29b-41d4-a716-446655440000",
    ),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Get messages from a conversation.
    Returns ciphertext envelopes only - client decrypts locally.
    """
    return service_get_dm_messages(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        limit=limit,
        since=since,
    )


@router.post("/messages/{message_id}/delivered", response_model=MarkDeliveredResponse)
async def mark_delivered(
    message_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Mark a message as delivered (metadata only).
    """
    return service_mark_dm_delivered(
        db, current_user=current_user, message_id=message_id
    )


@router.post("/messages/{message_id}/read", response_model=MarkReadResponse)
async def mark_read(
    message_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Mark a message as read (metadata only).
    """
    return service_mark_dm_read(db, current_user=current_user, message_id=message_id)
