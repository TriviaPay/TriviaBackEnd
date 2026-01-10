from fastapi import APIRouter

from . import notifications, onesignal, pusher_auth

router = APIRouter()
router.include_router(notifications.router)
router.include_router(onesignal.router)
router.include_router(pusher_auth.router)
