from fastapi import APIRouter

from . import faq

router = APIRouter()

router.include_router(faq.router)
