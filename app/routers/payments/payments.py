"""Payments Router - Stripe PaymentIntent for wallet top-ups and product purchases."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_db
from app.dependencies import get_current_user
from app.models.user import User

from .schemas import PaymentConfigResponse, PaymentSheetInitRequest, PaymentSheetResponse
from .service import (
    get_payment_config as service_get_payment_config,
    initialize_payment_sheet as service_initialize_payment_sheet,
)

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.get("/config", response_model=PaymentConfigResponse)
async def get_payment_config(user: User = Depends(get_current_user)):
    return service_get_payment_config()


@router.post("/payment-sheet", response_model=PaymentSheetResponse)
async def initialize_payment_sheet(
    request: PaymentSheetInitRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await service_initialize_payment_sheet(db, user=user, request=request)
