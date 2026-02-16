"""
Async Wallet Models
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db import Base


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    amount_minor = Column(BigInteger, nullable=False)
    currency = Column(String, nullable=False)
    kind = Column(
        String, nullable=False
    )  # deposit, withdraw, refund, fee, adjustment, etc.
    external_ref_type = Column(String, nullable=True)
    external_ref_id = Column(String, nullable=True)
    event_id = Column(String, nullable=True)
    idempotency_key = Column(String, nullable=True)
    livemode = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="wallet_transactions")


class IapReceipt(Base):
    __tablename__ = "iap_receipts"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    platform = Column(String, nullable=False)  # apple or google
    transaction_id = Column(String, nullable=False)
    original_transaction_id = Column(String, nullable=True)
    web_order_line_item_id = Column(String, nullable=True)
    product_id = Column(String, nullable=False)
    bundle_id = Column(String, nullable=True)
    environment = Column(String, nullable=True)  # sandbox or production
    product_type = Column(String, nullable=True)  # consumable, non_consumable, subscription
    receipt_data = Column(Text, nullable=True)
    purchase_token = Column(String, nullable=True)
    purchase_time_ms = Column(BigInteger, nullable=True)
    purchase_state = Column(Integer, nullable=True)
    acknowledgement_state = Column(Integer, nullable=True)
    revocation_date = Column(DateTime, nullable=True)
    revocation_reason = Column(String, nullable=True)
    app_account_token = Column(String, nullable=True)
    status = Column(String, nullable=False)  # received, verified, credited, revoked, failed
    credited_amount_minor = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    user = relationship("User", back_populates="iap_receipts")

    __table_args__ = (
        UniqueConstraint(
            "platform", "transaction_id", name="uq_iap_receipts_platform_transaction"
        ),
        UniqueConstraint(
            "platform", "purchase_token", name="uq_iap_receipts_platform_purchase_token"
        ),
    )


class IapEvent(Base):
    __tablename__ = "iap_events"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, index=True)
    platform = Column(String, nullable=False)
    event_id = Column(String, nullable=False, unique=True)
    notification_type = Column(String, nullable=True)
    subtype = Column(String, nullable=True)
    transaction_id = Column(String, nullable=True)
    purchase_token = Column(String, nullable=True)
    status = Column(String, nullable=False, default="received")
    raw_payload = Column(Text, nullable=True)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)
