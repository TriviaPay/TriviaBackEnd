import logging

from fastapi import APIRouter

from . import iap, wallet

logger = logging.getLogger(__name__)

router = APIRouter()
router.include_router(wallet.router)
router.include_router(iap.router)

try:
    from . import stripe_router

    router.include_router(stripe_router.router)
except Exception:
    logger.exception("Stripe router import failed — Stripe endpoints disabled")

try:
    from . import paypal_router

    router.include_router(paypal_router.router)
except Exception:
    logger.exception("PayPal router import failed — PayPal endpoints disabled")
