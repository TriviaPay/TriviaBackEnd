from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.db import get_db
from models import User
from routers.dependencies import get_current_user, verify_admin

from .schemas import AppVersionResponse, AppVersionUpsertRequest
from .service import (
    get_latest_app_version as service_get_latest_app_version,
    upsert_latest_app_version as service_upsert_latest_app_version,
)

router = APIRouter(prefix="/app-versions", tags=["App Versions"])


@router.get("/latest", response_model=AppVersionResponse)
def get_latest_app_version(os: str, db: Session = Depends(get_db)):
    return service_get_latest_app_version(db, os=os)


@router.put("/latest", response_model=AppVersionResponse)
def upsert_latest_app_version(
    payload: AppVersionUpsertRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_admin(db, current_user)
    return service_upsert_latest_app_version(
        db, os=payload.os, latest_version=payload.latest_version
    )
