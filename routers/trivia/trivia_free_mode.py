"""Free mode trivia endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import SubmitAnswerRequest
from .service import (
    free_mode_current_question as service_free_mode_current_question,
    free_mode_double_gems as service_free_mode_double_gems,
    free_mode_leaderboard as service_free_mode_leaderboard,
    free_mode_status as service_free_mode_status,
    get_free_mode_questions as service_get_free_mode_questions,
    submit_free_mode_answer as service_submit_free_mode_answer,
)

router = APIRouter(prefix="/trivia/free-mode", tags=["trivia-free-mode"])


@router.get("/questions")
async def get_free_mode_questions(
    user = Depends(get_current_user), db: Session = Depends(get_db)
):
    return service_get_free_mode_questions(db, user)


@router.get("/current-question")
async def get_current_free_mode_question(
    user = Depends(get_current_user), db: Session = Depends(get_db)
):
    return service_free_mode_current_question(db, user=user)


@router.post("/submit-answer")
async def submit_free_mode_answer(
    request: SubmitAnswerRequest,
    user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service_submit_free_mode_answer(db, user, request.question_id, request.answer)


@router.get("/leaderboard")
async def get_free_mode_leaderboard(
    draw_date: Optional[str] = None,
    user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service_free_mode_leaderboard(db, user=user, draw_date=draw_date)


@router.post("/double-gems")
async def double_gems_after_win(
    draw_date: Optional[str] = None,
    user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service_free_mode_double_gems(db, user=user, draw_date=draw_date)


@router.get("/status")
async def get_free_mode_status(
    user = Depends(get_current_user), db: Session = Depends(get_db)
):
    return service_free_mode_status(db, user=user)
