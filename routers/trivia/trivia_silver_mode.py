"""Silver mode trivia endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import SubmitAnswerRequest
from .service import (
    silver_mode_get_question as service_silver_mode_get_question,
    silver_mode_leaderboard as service_silver_mode_leaderboard,
    silver_mode_status as service_silver_mode_status,
    silver_mode_submit_answer as service_silver_mode_submit_answer,
)

router = APIRouter(prefix="/trivia/silver-mode", tags=["trivia-silver-mode"])


@router.get("/question")
async def get_silver_mode_question(
    user = Depends(get_current_user), db: Session = Depends(get_db)
):
    return await service_silver_mode_get_question(db, user=user)


@router.post("/submit-answer")
async def submit_silver_mode_answer(
    request: SubmitAnswerRequest,
    user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await service_silver_mode_submit_answer(db, user=user, request=request)


@router.get("/status")
async def get_silver_mode_status(
    user = Depends(get_current_user), db: Session = Depends(get_db)
):
    return service_silver_mode_status(db, user=user)


@router.get("/leaderboard")
async def get_silver_mode_leaderboard(
    draw_date: Optional[str] = Query(None),
    user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service_silver_mode_leaderboard(db, draw_date=draw_date)
