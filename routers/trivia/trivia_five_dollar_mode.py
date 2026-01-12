"""Bronze mode trivia endpoints (legacy filename kept for compatibility)."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import SubmitAnswerRequest
from .service import (
    bronze_mode_get_question as service_bronze_mode_get_question,
    bronze_mode_leaderboard as service_bronze_mode_leaderboard,
    bronze_mode_status as service_bronze_mode_status,
    bronze_mode_submit_answer as service_bronze_mode_submit_answer,
)

router = APIRouter(prefix="/trivia/bronze-mode", tags=["trivia-bronze-mode"])


@router.get("/question")
async def get_bronze_mode_question(
    user = Depends(get_current_user), db: Session = Depends(get_db)
):
    return await service_bronze_mode_get_question(db, user=user)


@router.post("/submit-answer")
async def submit_bronze_mode_answer(
    request: SubmitAnswerRequest,
    user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await service_bronze_mode_submit_answer(db, user=user, request=request)


@router.get("/status")
async def get_bronze_mode_status(
    user = Depends(get_current_user), db: Session = Depends(get_db)
):
    return service_bronze_mode_status(db, user=user)


@router.get("/leaderboard")
async def get_bronze_mode_leaderboard(
    draw_date: Optional[str] = Query(None),
    user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service_bronze_mode_leaderboard(db, draw_date=draw_date)
