from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import (
    GlobalChatCleanupResponse,
    GlobalChatMessagesResponse,
    GlobalChatSendMessageRequest,
    GlobalChatSendResponse,
)
from .service import (
    cleanup_global_chat_messages as service_cleanup_global_chat_messages,
    get_global_chat_messages as service_get_global_chat_messages,
    publish_to_pusher_global as service_publish_to_pusher_global,
    send_global_chat_message as service_send_global_chat_message,
    send_push_for_global_chat_sync as service_send_push_for_global_chat_sync,
)

router = APIRouter(prefix="/global-chat", tags=["Global Chat"])


@router.get("", response_model=GlobalChatMessagesResponse)
async def get_messages(
    limit: int = Query(50, ge=1, le=100),
    before: Optional[int] = Query(
        None, description="Return messages before this message ID"
    ),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    return await service_get_global_chat_messages(
        db, current_user=current_user, limit=limit, before=before
    )


@router.post("/send", response_model=GlobalChatSendResponse)
async def send_message(
    request: GlobalChatSendMessageRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    result = await service_send_global_chat_message(
        db, current_user=current_user, request=request
    )

    response = result["response"]
    if response.get("duplicate"):
        return response

    if not result.get("event_enqueued"):
        pusher_args = result.get("pusher_args")
        push_args = result.get("push_args")

        if pusher_args:
            background_tasks.add_task(service_publish_to_pusher_global, **pusher_args)
        if push_args:
            background_tasks.add_task(service_send_push_for_global_chat_sync, **push_args)

    return response


@router.delete("/cleanup", response_model=GlobalChatCleanupResponse)
async def cleanup_messages(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    return service_cleanup_global_chat_messages(db, current_user=current_user)
