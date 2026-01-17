from fastapi import APIRouter

from . import faq

router = APIRouter(tags=["Support"])

router.include_router(faq.router)
