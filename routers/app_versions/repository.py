"""App versions repository layer."""

from sqlalchemy.orm import Session


def get_app_version_by_os(db: Session, *, os: str):
    from models import AppVersion

    return db.query(AppVersion).filter(AppVersion.os == os).first()


def upsert_latest_app_version(db: Session, *, os: str, latest_version: str):
    from models import AppVersion

    app_version = get_app_version_by_os(db, os=os)
    if app_version:
        app_version.latest_version = latest_version
    else:
        app_version = AppVersion(os=os, latest_version=latest_version)
        db.add(app_version)
    db.commit()
    db.refresh(app_version)
    return app_version

