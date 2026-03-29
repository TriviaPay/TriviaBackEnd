"""
Async Product Models - Avatars, Frames, Gem Packages, Badges
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.db import Base


class Avatar(Base):
    __tablename__ = "avatars"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    product_id = Column(String(5), unique=True, nullable=True, index=True)
    price_minor = Column(BigInteger, nullable=True)
    product_type = Column(String, nullable=False, default="non_consumable")
    is_premium = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    @property
    def price_usd(self):
        """Compute price_usd from price_minor"""
        if self.price_minor is not None:
            return self.price_minor / 100.0
        return None


class Frame(Base):
    __tablename__ = "frames"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    product_id = Column(String(5), unique=True, nullable=True, index=True)
    price_minor = Column(BigInteger, nullable=True)
    product_type = Column(String, nullable=False, default="non_consumable")
    is_premium = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    @property
    def price_usd(self):
        """Compute price_usd from price_minor"""
        if self.price_minor is not None:
            return self.price_minor / 100.0
        return None


class GemPackageConfig(Base):
    __tablename__ = "gem_package_config"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(String(5), unique=True, nullable=True, index=True)
    price_minor = Column(BigInteger, nullable=True)
    product_type = Column(String, nullable=False, default="consumable")
    gems_amount = Column(Integer, nullable=False)
    is_one_time = Column(Boolean, default=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def price_usd(self):
        """Compute price_usd from price_minor"""
        if self.price_minor is not None:
            return self.price_minor / 100.0
        return None


class Badge(Base):
    __tablename__ = "badges"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    product_id = Column(String(5), unique=True, nullable=True, index=True)
    price_minor = Column(BigInteger, nullable=True)
    product_type = Column(String, nullable=False, default="non_consumable")
    level = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    @property
    def price_usd(self):
        """Compute price_usd from price_minor"""
        if self.price_minor is not None:
            return self.price_minor / 100.0
        return None


class UserAvatar(Base):
    __tablename__ = "user_avatars"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    avatar_id = Column(String, ForeignKey("avatars.id"), nullable=False)
    purchase_date = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "avatar_id", name="uq_user_avatar"),
    )


class UserFrame(Base):
    __tablename__ = "user_frames"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    frame_id = Column(String, ForeignKey("frames.id"), nullable=False)
    purchase_date = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "frame_id", name="uq_user_frame"),
    )
