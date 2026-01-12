from typing import Protocol

from sqlalchemy.orm import Session


class AuthPort(Protocol):
    def get_user_by_id(self, db: Session, *, account_id: int): ...

    def get_user_by_email(self, db: Session, *, email: str): ...

    def get_user_by_username(self, db: Session, *, username: str): ...

