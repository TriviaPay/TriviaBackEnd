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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db import Base


class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(BigInteger, primary_key=True, index=True)
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


class WithdrawalRequest(Base):
    __tablename__ = "withdrawal_requests"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    amount_minor = Column(BigInteger, nullable=False)
    currency = Column(String, nullable=False)
    type = Column(String, nullable=False)  # standard or instant
    status = Column(
        String, nullable=False
    )  # pending_review, processing, paid, failed, rejected
    fee_minor = Column(BigInteger, default=0, nullable=False)
    stripe_payout_id = Column(String, nullable=True)
    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)
    admin_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=True)
    admin_notes = Column(Text, nullable=True)
    livemode = Column(Boolean, default=False, nullable=False)

    # Relationships
    user = relationship(
        "User", foreign_keys=[user_id], back_populates="withdrawal_requests"
    )
    admin = relationship("User", foreign_keys=[admin_id])


class IapReceipt(Base):
    __tablename__ = "iap_receipts"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    platform = Column(String, nullable=False)  # apple or google
    transaction_id = Column(String, nullable=False)
    product_id = Column(String, nullable=False)
    receipt_data = Column(Text, nullable=True)
    status = Column(String, nullable=False)  # verified, failed, consumed
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
    )
