from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db import get_db
from models import AppVersion, User
from routers.dependencies import get_current_user, verify_admin

from .schemas import AppVersionResponse, AppVersionUpsertRequest

router = APIRouter(prefix="/app-versions", tags=["App Versions"])


@router.get("/latest", response_model=AppVersionResponse)
def get_latest_app_version(os: str, db: Session = Depends(get_db)):
    app_version = db.query(AppVersion).filter(AppVersion.os == os).first()
    if not app_version:
        raise HTTPException(status_code=404, detail="App version not found")
    return app_version


@router.put("/latest", response_model=AppVersionResponse)
def upsert_latest_app_version(
    payload: AppVersionUpsertRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_admin(db, current_user)

    app_version = db.query(AppVersion).filter(AppVersion.os == payload.os).first()
    if app_version:
        app_version.latest_version = payload.latest_version
    else:
        app_version = AppVersion(
            os=payload.os,
            latest_version=payload.latest_version,
        )
        db.add(app_version)
    db.commit()
    db.refresh(app_version)
    return app_version
