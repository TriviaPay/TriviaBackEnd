from fastapi import APIRouter

from . import admin_withdrawals, iap, payments, stripe_connect, stripe_webhook, wallet

router = APIRouter()
router.include_router(wallet.router)
router.include_router(payments.router)
router.include_router(stripe_webhook.router)
router.include_router(stripe_connect.router)
router.include_router(iap.router)
router.include_router(admin_withdrawals.router)
