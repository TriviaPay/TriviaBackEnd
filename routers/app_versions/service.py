"""App versions service layer."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import repository


def get_latest_app_version(db: Session, *, os: str):
    app_version = repository.get_app_version_by_os(db, os=os)
    if not app_version:
        raise HTTPException(status_code=404, detail="App version not found")
    return app_version


def upsert_latest_app_version(db: Session, *, os: str, latest_version: str):
    return repository.upsert_latest_app_version(db, os=os, latest_version=latest_version)

