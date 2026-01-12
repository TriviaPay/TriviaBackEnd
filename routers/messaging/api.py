from fastapi import APIRouter

from core.config import PRESENCE_ENABLED

from . import chat_mute, global_chat, presence, private_chat

router = APIRouter()
router.include_router(global_chat.router)
router.include_router(private_chat.router)
router.include_router(chat_mute.router)
if PRESENCE_ENABLED:
    router.include_router(presence.router)
