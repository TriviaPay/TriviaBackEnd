from fastapi import APIRouter, BackgroundTasks, Depends, Header
from sqlalchemy.orm import Session

from core.db import get_db

from .schemas import TriviaReminderRequest
from .service import (
    internal_daily_revenue_update as service_internal_daily_revenue_update,
    internal_free_mode_draw as service_internal_free_mode_draw,
    internal_health as service_internal_health,
    internal_mode_draw as service_internal_mode_draw,
    internal_monthly_reset as service_internal_monthly_reset,
    internal_trivia_reminder as service_internal_trivia_reminder,
    internal_weekly_rewards_reset as service_internal_weekly_rewards_reset,
)

router = APIRouter(prefix="/internal", tags=["Internal"])


@router.post("/free-mode-draw")
def internal_free_mode_draw(
    secret: str = Header(..., alias="X-Secret"),
    db: Session = Depends(get_db),
):
    return service_internal_free_mode_draw(db, secret=secret)


@router.post("/mode-draw/{mode_id}")
def internal_mode_draw(
    mode_id: str,
    secret: str = Header(..., alias="X-Secret"),
    db: Session = Depends(get_db),
):
    return service_internal_mode_draw(db, secret=secret, mode_id=mode_id)


@router.post("/trivia-reminder")
def send_trivia_reminder(
    request: TriviaReminderRequest,
    background_tasks: BackgroundTasks,
    secret: str = Header(..., alias="X-Secret"),
    db: Session = Depends(get_db),
):
    return service_internal_trivia_reminder(
        db, secret=secret, request=request, background_tasks=background_tasks
    )


@router.post("/monthly-reset")
def internal_monthly_reset(
    secret: str = Header(..., alias="X-Secret"),
    db: Session = Depends(get_db),
):
    return service_internal_monthly_reset(db, secret=secret)


@router.post("/weekly-rewards-reset")
def internal_weekly_rewards_reset(
    secret: str = Header(..., alias="X-Secret"),
    db: Session = Depends(get_db),
):
    return service_internal_weekly_rewards_reset(db, secret=secret)


@router.post("/daily-revenue-update")
def internal_daily_revenue_update(
    secret: str = Header(..., alias="X-Secret"),
    db: Session = Depends(get_db),
):
    return service_internal_daily_revenue_update(db, secret=secret)


@router.get("/health")
def internal_health():
    return service_internal_health()
