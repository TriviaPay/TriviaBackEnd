from fastapi import APIRouter

from . import (
    draw,
    internal,
    rewards,
    trivia_five_dollar_mode,
    trivia_free_mode,
    trivia_live_chat,
    trivia_silver_mode,
)

router = APIRouter()
router.include_router(draw.router)
router.include_router(trivia_free_mode.router)
router.include_router(trivia_five_dollar_mode.router)
router.include_router(trivia_silver_mode.router)
router.include_router(trivia_live_chat.router)
router.include_router(rewards.router)
router.include_router(internal.router)
