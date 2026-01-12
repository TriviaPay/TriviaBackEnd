"""Stripe Webhook Router - Handles Stripe webhook events."""

from typing import Optional

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_async_db

from .service import process_stripe_webhook as service_process_stripe_webhook

router = APIRouter(prefix="/stripe/webhook", tags=["Stripe Webhooks"])


@router.post("")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="stripe-signature"),
    db: AsyncSession = Depends(get_async_db),
):
    return await service_process_stripe_webhook(
        db, request=request, stripe_signature=stripe_signature
    )
