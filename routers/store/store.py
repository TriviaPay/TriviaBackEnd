from typing import List

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import BuyGemsRequest, GemPackageResponse, PurchaseResponse
from .service import buy_gems_with_wallet as service_buy_gems_with_wallet
from .service import get_gem_packages as service_get_gem_packages

router = APIRouter(prefix="/store", tags=["Store"])


@router.post("/buy-gems", response_model=PurchaseResponse)
async def buy_gems_with_wallet(
    request: BuyGemsRequest = Body(..., description="Gem purchase details"),
    user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Buy gems using wallet balance"""
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return service_buy_gems_with_wallet(db, user, request.package_id)


@router.get("/gem-packages", response_model=List[GemPackageResponse])
async def get_gem_packages(
    user = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Get all available gem packages with presigned URLs for images"""
    return service_get_gem_packages(db)
