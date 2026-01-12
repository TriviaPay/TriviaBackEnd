from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from core.db import get_db

from .service import refresh_session

router = APIRouter(prefix="/auth", tags=["Refresh"])


@router.post("/refresh")
async def refresh_session_endpoint(request: Request, db: Session = Depends(get_db)):
    return refresh_session(request, db)
