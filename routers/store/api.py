from fastapi import APIRouter

from . import badges, cosmetics, store

router = APIRouter()
router.include_router(store.router)
router.include_router(cosmetics.router)
router.include_router(badges.router)
