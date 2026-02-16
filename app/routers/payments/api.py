from fastapi import APIRouter

from . import iap, wallet

router = APIRouter()
router.include_router(wallet.router)
router.include_router(iap.router)
