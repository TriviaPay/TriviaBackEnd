"""
Async App Version Model
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.db import Base


class AppVersion(Base):
    __tablename__ = "app_versions"

    id = Column(Integer, primary_key=True, index=True)
    os = Column(String, nullable=False, unique=True, index=True)
    latest_version = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
