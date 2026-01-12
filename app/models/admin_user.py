from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db import Base


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, index=True)
    singleton_key = Column(String, nullable=False, unique=True, default="primary")
    user_id = Column(
        BigInteger, ForeignKey("users.account_id"), nullable=False, unique=True
    )
    email = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship(
        "User",
        primaryjoin="AdminUser.user_id==User.account_id",
        foreign_keys=[user_id],
        backref="admin_profile",
    )

    __table_args__ = (
        UniqueConstraint("singleton_key", name="uq_admin_users_singleton"),
    )
