"""
Async Product Models - Avatars, Frames, Gem Packages, Badges
"""
from sqlalchemy import Column, BigInteger, String, Boolean, DateTime, Integer, Float
from app.db import Base
from datetime import datetime


class Avatar(Base):
    __tablename__ = "avatars"
    
    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    product_id = Column(String(5), unique=True, nullable=True, index=True)
    price_minor = Column(BigInteger, nullable=True)
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
    level = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    @property
    def price_usd(self):
        """Compute price_usd from price_minor"""
        if self.price_minor is not None:
            return self.price_minor / 100.0
        return None

