"""User lookup facade.

Domains should not query the `User` model directly. Instead, call these helpers which
delegate to the Auth/Profile domain internal service API.
"""

from typing import Optional

from sqlalchemy.orm import Session


def get_user_by_id(db: Session, *, account_id: int):
    from routers.auth import service as auth_service

    return auth_service.get_user_by_id(db, account_id=account_id)


def get_user_by_id_for_update(db: Session, *, account_id: int):
    from routers.auth import service as auth_service

    return auth_service.get_user_by_id_for_update(db, account_id=account_id)


def get_user_by_email(db: Session, *, email: str):
    from routers.auth import service as auth_service

    return auth_service.get_user_by_email(db, email=email)


def get_user_by_username(db: Session, *, username: str):
    from routers.auth import service as auth_service

    return auth_service.get_user_by_username(db, username=username)


def get_user_by_descope_id(db: Session, *, descope_user_id: str):
    from routers.auth import service as auth_service

    return auth_service.get_user_by_descope_id(db, descope_user_id=descope_user_id)


def get_users_by_ids(db: Session, *, account_ids: list[int]):
    from routers.auth import service as auth_service

    return auth_service.get_users_by_ids(db, account_ids=account_ids)


def maybe_get_user_by_id(db: Session, *, account_id: int):
    user = get_user_by_id(db, account_id=account_id)
    return user


def maybe_get_user_by_email(db: Session, *, email: str):
    user = get_user_by_email(db, email=email)
    return user


def maybe_get_user_by_username(db: Session, *, username: str):
    user = get_user_by_username(db, username=username)
    return user
