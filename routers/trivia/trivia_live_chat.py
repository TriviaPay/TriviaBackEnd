from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import TriviaLiveChatSendMessageRequest
from .service import (
    trivia_live_chat_debug_config as service_trivia_live_chat_debug_config,
    trivia_live_chat_get_likes as service_trivia_live_chat_get_likes,
    trivia_live_chat_get_messages as service_trivia_live_chat_get_messages,
    trivia_live_chat_like as service_trivia_live_chat_like,
    trivia_live_chat_send_message as service_trivia_live_chat_send_message,
    trivia_live_chat_status as service_trivia_live_chat_status,
)

router = APIRouter(prefix="/trivia-live-chat", tags=["Trivia Live Chat"])


@router.post("/send")
async def send_trivia_live_message(
    request: TriviaLiveChatSendMessageRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    return await service_trivia_live_chat_send_message(
        db, current_user=current_user, request=request, background_tasks=background_tasks
    )


@router.get("/messages")
async def get_trivia_live_messages(
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    return await service_trivia_live_chat_get_messages(
        db, current_user=current_user, limit=limit
    )


@router.get("/debug-config")
async def debug_config():
    return await service_trivia_live_chat_debug_config()


@router.get("/status")
async def status(
    db: Session = Depends(get_db), current_user = Depends(get_current_user)
):
    return await service_trivia_live_chat_status(db, current_user=current_user)


@router.post("/like")
async def like(
    db: Session = Depends(get_db), current_user = Depends(get_current_user)
):
    return await service_trivia_live_chat_like(db, current_user=current_user)


@router.get("/likes")
async def likes(
    db: Session = Depends(get_db), current_user = Depends(get_current_user)
):
    return await service_trivia_live_chat_get_likes(db, current_user=current_user)
