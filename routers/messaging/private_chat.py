from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import (
    PrivateChatAcceptRejectRequest,
    PrivateChatBlockUserRequest,
    PrivateChatSendMessageRequest,
)
from .service import (
    accept_reject_private_chat as service_accept_reject_private_chat,
    block_private_chat_user as service_block_private_chat_user,
    get_private_conversation as service_get_private_conversation,
    get_private_messages as service_get_private_messages,
    list_private_chat_blocks as service_list_private_chat_blocks,
    list_private_conversations as service_list_private_conversations,
    mark_conversation_read as service_mark_conversation_read,
    mark_private_message_delivered as service_mark_private_message_delivered,
    send_private_message as service_send_private_message,
    send_private_typing_indicator as service_send_private_typing_indicator,
    send_private_typing_stop as service_send_private_typing_stop,
    unblock_private_chat_user as service_unblock_private_chat_user,
)

router = APIRouter(prefix="/private-chat", tags=["Private Chat"])


@router.post("/send")
async def send_private_message(
    request: PrivateChatSendMessageRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Send private message - creates conversation if needed."""
    return await service_send_private_message(
        db,
        current_user=current_user,
        request=request,
        background_tasks=background_tasks,
    )


@router.post("/accept-reject")
async def accept_reject_chat(
    request: PrivateChatAcceptRejectRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Accept or reject a chat request."""
    return await service_accept_reject_private_chat(
        db,
        current_user=current_user,
        request=request,
        background_tasks=background_tasks,
    )


@router.get("/conversations")
async def list_private_conversations(
    db: Session = Depends(get_db), current_user = Depends(get_current_user)
):
    """List all private chat conversations with unread counts."""
    return await service_list_private_conversations(db, current_user=current_user)


@router.get("/conversations/{conversation_id}/messages")
async def get_private_messages(
    conversation_id: int,
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Get messages from a private conversation with read status."""
    return await service_get_private_messages(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        limit=limit,
    )


@router.post("/conversations/{conversation_id}/mark-read")
async def mark_conversation_read(
    conversation_id: int,
    background_tasks: BackgroundTasks,
    message_id: Optional[int] = Query(
        None, description="Message ID to mark as read up to (defaults to latest)"
    ),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Mark conversation as read up to a specific message ID."""
    return await service_mark_conversation_read(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        message_id=message_id,
        background_tasks=background_tasks,
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Get conversation details."""
    return await service_get_private_conversation(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
    )


@router.post("/conversations/{conversation_id}/typing")
async def send_typing_indicator(
    conversation_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Send typing indicator to conversation."""
    return await service_send_private_typing_indicator(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        background_tasks=background_tasks,
    )


@router.post("/conversations/{conversation_id}/typing-stop")
async def send_typing_stop(
    conversation_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Send typing stop indicator to conversation."""
    return await service_send_private_typing_stop(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        background_tasks=background_tasks,
    )


@router.post("/messages/{message_id}/mark-delivered")
async def mark_message_delivered(
    message_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Mark a message as delivered."""
    return await service_mark_private_message_delivered(
        db,
        current_user=current_user,
        message_id=message_id,
        background_tasks=background_tasks,
    )


@router.post("/block")
async def block_user(
    request: PrivateChatBlockUserRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Block a user from sending private messages."""
    return service_block_private_chat_user(
        db, current_user=current_user, blocked_user_id=request.blocked_user_id
    )


@router.delete("/block/{blocked_user_id}")
async def unblock_user(
    blocked_user_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Unblock a user."""
    return service_unblock_private_chat_user(
        db, current_user=current_user, blocked_user_id=blocked_user_id
    )


@router.get("/blocks")
async def list_blocks(
    db: Session = Depends(get_db), current_user = Depends(get_current_user)
):
    """List all users blocked by the current user."""
    return service_list_private_chat_blocks(db, current_user=current_user)
