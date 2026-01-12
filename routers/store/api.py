from fastapi import APIRouter

from . import cosmetics, store

router = APIRouter()
router.include_router(store.router)
router.include_router(cosmetics.router)
