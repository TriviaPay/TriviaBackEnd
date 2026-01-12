"""
Async User Model
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, Integer, String
from sqlalchemy.orm import relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    account_id = Column(BigInteger, primary_key=True)
    descope_user_id = Column(String, unique=True, index=True, nullable=True)
    device_uuid = Column(String, nullable=True)
    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, unique=True, index=True, nullable=False)
    mobile = Column(String, nullable=True)
    country_code = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    middle_name = Column(String, nullable=True)
    ssn = Column(String, nullable=True)
    password = Column(String, nullable=True)
    profile_pic_url = Column(String, nullable=True)
    notification_on = Column(Boolean, default=True)
    street_1 = Column(String, nullable=True)
    street_2 = Column(String, nullable=True)
    suite_or_apt_number = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    zip = Column(String, nullable=True)
    country = Column(String, nullable=True)
    gender = Column(String, nullable=True)
    date_of_birth = Column(Date, nullable=True)
    referral_code = Column(String(5), unique=True, nullable=True)
    referred_by = Column(String(5), nullable=True)
    referral_count = Column(Integer, default=0)

    subscriber_number = Column(String, nullable=True)
    username_updated = Column(Boolean, default=False)
    subscription_flag = Column(Boolean, default=False)
    sign_up_date = Column(DateTime, default=datetime.utcnow, nullable=False)

    gems = Column(Integer, default=0)
    daily_eligibility_flag = Column(Boolean, default=False)
    badge_id = Column(String, nullable=True)

    # Wallet fields
    wallet_balance = Column(
        BigInteger, nullable=True
    )  # Deprecated, use wallet_balance_minor
    wallet_balance_minor = Column(BigInteger, nullable=True)
    wallet_currency = Column(String, default="usd")
    total_spent = Column(BigInteger, nullable=True)
    last_wallet_update = Column(DateTime, nullable=True)

    # Stripe fields
    stripe_customer_id = Column(String, nullable=True, index=True)
    stripe_connect_account_id = Column(String(255), nullable=True)
    instant_withdrawal_enabled = Column(Boolean, default=True, nullable=False)
    instant_withdrawal_daily_limit_minor = Column(
        BigInteger, default=100000, nullable=False
    )

    selected_avatar_id = Column(String, nullable=True)
    selected_frame_id = Column(String, nullable=True)

    # Relationships
    wallet_transactions = relationship("WalletTransaction", back_populates="user")
    withdrawal_requests = relationship(
        "WithdrawalRequest",
        foreign_keys="[WithdrawalRequest.user_id]",
        back_populates="user",
    )
    iap_receipts = relationship("IapReceipt", back_populates="user")

    @property
    def price_usd(self):
        """Compute price_usd from wallet_balance_minor for backward compatibility"""
        if self.wallet_balance_minor is not None:
            return self.wallet_balance_minor / 100.0
        return 0.0


# Ensure dependent models are imported so SQLAlchemy can resolve string relationships
# when this module is imported directly (e.g., via auth dependencies).
from app.models import admin_user as _admin_user  # noqa: E402,F401
from app.models import wallet as _wallet  # noqa: E402,F401
