from fastapi import APIRouter

from . import iap, stripe_router, wallet

router = APIRouter()
router.include_router(wallet.router)
router.include_router(iap.router)
router.include_router(stripe_router.router)
