from fastapi import APIRouter

from . import admin, login, profile, refresh

router = APIRouter()
router.include_router(login.router)
router.include_router(refresh.router)
router.include_router(profile.router)
router.include_router(admin.router)
