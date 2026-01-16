import random
import uuid
from datetime import date, datetime
from enum import Enum as PyEnum

from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import relationship

from core.db import Base


def generate_account_id():
    """Generate a 10-digit random unique number."""
    return int("".join(str(random.randint(0, 9)) for _ in range(10)))


# =================================
#  Users Table
# =================================
class User(Base):
    __tablename__ = "users"

    account_id = Column(
        BigInteger,
        primary_key=True,
        unique=True,
        index=True,
        nullable=False,
        default=generate_account_id,
    )
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
    gender = Column(String, nullable=True)  # Added gender field
    date_of_birth = Column(Date, nullable=True)
    referral_code = Column(String(5), unique=True, nullable=True)
    referred_by = Column(String(5), nullable=True)
    referral_count = Column(Integer, default=0)
    subscriber_number = Column(String, nullable=True)
    username_updated = Column(
        Boolean, default=False
    )  # Track if username has been updated before
    subscription_flag = Column(Boolean, default=False)
    sign_up_date = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Added fields for trivia game
    gems = Column(Integer, default=0)  # Track user's gems
    level = Column(
        Integer, default=1, nullable=False
    )  # User level - increases by 1 for every 100 questions answered (right or wrong)

    # Daily draw eligibility tracking
    daily_eligibility_flag = Column(
        Boolean, default=False
    )  # True if user answered all 3 questions correctly today

    # Badge fields - now references trivia_mode_config.mode_id instead of badges.id
    badge_id = Column(
        String, ForeignKey("trivia_mode_config.mode_id"), nullable=True
    )  # Reference to mode_id (badge functionality merged into trivia_mode_config)

    # Wallet fields
    wallet_balance = Column(
        Float, default=0.0
    )  # Deprecated: use wallet_balance_minor instead
    wallet_balance_minor = Column(
        BigInteger, nullable=True
    )  # Wallet balance in minor units (cents) - nullable until migration
    wallet_currency = Column(String, default="usd")  # Currency of wallet balance
    total_spent = Column(Float, default=0.0)  # Total amount spent in the app
    last_wallet_update = Column(DateTime, nullable=True)  # Last time wallet was updated

    # Stripe integration fields
    stripe_customer_id = Column(
        String, nullable=True, index=True
    )  # Stripe customer ID for payment methods
    stripe_connect_account_id = Column(
        String(255), nullable=True
    )  # Stripe Connect account ID
    instant_withdrawal_enabled = Column(
        Boolean, default=True, nullable=False
    )  # Enable instant withdrawals
    instant_withdrawal_daily_limit_minor = Column(
        BigInteger, default=100000, nullable=False
    )  # Daily limit in cents ($1000)

    # Cosmetic selections
    selected_avatar_id = Column(String, nullable=True)  # Currently selected avatar ID
    selected_frame_id = Column(String, nullable=True)  # Currently selected frame ID

    # Relationships
    # TriviaQuestionsEntries removed - legacy table
    badge_info = relationship(
        "TriviaModeConfig", foreign_keys=[badge_id], uselist=False
    )  # Badge functionality merged into TriviaModeConfig
    subscriptions = relationship("UserSubscription", back_populates="user")
    wallet_transactions = relationship("WalletTransaction", back_populates="user")
    withdrawal_requests = relationship(
        "WithdrawalRequest",
        foreign_keys="[WithdrawalRequest.user_id]",
        back_populates="user",
    )
    iap_receipts = relationship("IapReceipt", back_populates="user")
    device_versions = relationship("UserDeviceVersion", back_populates="user")


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, index=True)
    singleton_key = Column(String, nullable=False, unique=True, default="primary")
    user_id = Column(
        BigInteger, ForeignKey("users.account_id"), nullable=False, unique=True
    )
    email = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", backref="admin_profile")


# =================================
#  Legacy Trivia Tables - REMOVED
# =================================
# TriviaQuestionsEntries, Trivia, TriviaQuestionsDaily, TriviaUserDaily have been removed
# The new system uses mode-specific tables:
# - trivia_questions_free_mode, trivia_questions_bronze_mode, trivia_questions_silver_mode
# - trivia_questions_free_mode_daily, trivia_questions_bronze_mode_daily, trivia_questions_silver_mode_daily
# - trivia_user_free_mode_daily, trivia_user_bronze_mode_daily, trivia_user_silver_mode_daily


# =================================
#  New: Withdrawals Table
# =================================
class Withdrawal(Base):
    """
    account_id, amount, withdrawal_method, withdrawal_status,
    requested_at, processed_at
    """

    __tablename__ = "withdrawals"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    amount = Column(Float, nullable=False)
    withdrawal_method = Column(String, nullable=False)  # e.g. "bank", "paypal", ...
    withdrawal_status = Column(
        String, nullable=False
    )  # e.g. "requested", "completed", "failed"
    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)

    # Relationship to user if desired
    # Note: Withdrawal table is legacy - use WithdrawalRequest instead
    user = relationship("User", backref="withdrawals")


# =================================
#  Legacy Daily Questions Tables - REMOVED
# =================================
# TriviaQuestionsDaily and TriviaUserDaily have been removed
# Use mode-specific daily tables instead


# =================================
#  Daily Login Rewards Table
# =================================
class UserDailyRewards(Base):
    """Per-user weekly daily login rewards tracking (Monday-Sunday)"""

    __tablename__ = "user_daily_rewards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(
        BigInteger, ForeignKey("users.account_id"), nullable=False, index=True
    )
    week_start_date = Column(Date, nullable=False)  # Monday of the week
    day1_status = Column(Boolean, default=False, nullable=False)  # Monday
    day2_status = Column(Boolean, default=False, nullable=False)  # Tuesday
    day3_status = Column(Boolean, default=False, nullable=False)  # Wednesday
    day4_status = Column(Boolean, default=False, nullable=False)  # Thursday
    day5_status = Column(Boolean, default=False, nullable=False)  # Friday
    day6_status = Column(Boolean, default=False, nullable=False)  # Saturday
    day7_status = Column(Boolean, default=False, nullable=False)  # Sunday
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    user = relationship("User", backref="daily_rewards")
    __table_args__ = (
        UniqueConstraint("account_id", "week_start_date", name="uq_user_week_rewards"),
    )


# =================================
#  Cosmetics - Avatars Table
# =================================
class Avatar(Base):
    __tablename__ = "avatars"

    id = Column(String, primary_key=True, index=True)  # Unique ID for the avatar
    name = Column(String, nullable=False)  # Display name
    description = Column(String, nullable=True)  # Description of the avatar
    bucket = Column(String, nullable=True)  # Private storage bucket name
    object_key = Column(String, nullable=True)  # Private storage key
    mime_type = Column(String, nullable=True)  # e.g., image/png, application/json
    price_gems = Column(
        Integer, nullable=True
    )  # Price in gems (if purchasable with gems)
    product_id = Column(
        String(5), unique=True, nullable=True
    )  # Unique product ID (e.g., AV001)
    price_minor = Column(BigInteger, nullable=True)  # Price in minor units (cents)
    is_premium = Column(Boolean, default=False)  # Whether it's a premium avatar
    created_at = Column(
        DateTime, default=datetime.utcnow, nullable=False
    )  # When the avatar was added

    @property
    def price_usd(self):
        """Compute price_usd from price_minor"""
        if self.price_minor is not None:
            return self.price_minor / 100.0
        return None

    # Relationships
    users = relationship("UserAvatar", back_populates="avatar")


# =================================
#  Cosmetics - Frames Table
# =================================
class Frame(Base):
    __tablename__ = "frames"

    id = Column(String, primary_key=True, index=True)  # Unique ID for the frame
    name = Column(String, nullable=False)  # Display name
    description = Column(String, nullable=True)  # Description of the frame
    bucket = Column(String, nullable=True)  # Private storage bucket name
    object_key = Column(String, nullable=True)  # Private storage key
    mime_type = Column(String, nullable=True)  # e.g., image/png, application/json
    price_gems = Column(
        Integer, nullable=True
    )  # Price in gems (if purchasable with gems)
    product_id = Column(
        String(5), unique=True, nullable=True
    )  # Unique product ID (e.g., FR001)
    price_minor = Column(BigInteger, nullable=True)  # Price in minor units (cents)
    is_premium = Column(Boolean, default=False)  # Whether it's a premium frame
    created_at = Column(
        DateTime, default=datetime.utcnow, nullable=False
    )  # When the frame was added

    @property
    def price_usd(self):
        """Compute price_usd from price_minor"""
        if self.price_minor is not None:
            return self.price_minor / 100.0
        return None

    # Relationships
    users = relationship("UserFrame", back_populates="frame")


# =================================
#  User-Avatar Relation Table
# =================================
class UserAvatar(Base):
    __tablename__ = "user_avatars"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    avatar_id = Column(String, ForeignKey("avatars.id"), nullable=False)
    purchase_date = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", backref="owned_avatars")
    avatar = relationship("Avatar", back_populates="users")

    __table_args__ = (
        # Unique constraint ensures idempotent buys: one user can only own each avatar once
        UniqueConstraint("user_id", "avatar_id", name="uq_user_avatar"),
    )


# =================================
#  User-Frame Relation Table
# =================================
class UserFrame(Base):
    __tablename__ = "user_frames"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    frame_id = Column(String, ForeignKey("frames.id"), nullable=False)
    purchase_date = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", backref="owned_frames")
    frame = relationship("Frame", back_populates="users")

    __table_args__ = (
        # Unique constraint ensures idempotent buys: one user can only own each frame once
        UniqueConstraint("user_id", "frame_id", name="uq_user_frame"),
    )


# =================================
#  Badge Table - REMOVED
# =================================
# Badge functionality has been merged into TriviaModeConfig
# Use TriviaModeConfig with badge_* fields instead

# =================================
#  Trivia Draw Configuration - REMOVED (LEGACY)
# =================================
# TriviaDrawConfig and TriviaQuestionsWinners have been removed as legacy
# The new system uses mode-specific winner tables:
# - TriviaFreeModeWinners
# - TriviaBronzeModeWinners
# - TriviaSilverModeWinners
# Draw configuration is now handled per-mode via TriviaModeConfig.reward_distribution


# =================================
#  Gem Package Configuration
# =================================
class GemPackageConfig(Base):
    __tablename__ = "gem_package_config"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(
        String(5), unique=True, nullable=True
    )  # Unique product ID (e.g., GP001)
    price_minor = Column(BigInteger, nullable=True)  # Price in minor units (cents)
    gems_amount = Column(Integer, nullable=False)
    is_one_time = Column(Boolean, default=False)  # For one-time offers
    description = Column(String, nullable=True)
    bucket = Column(String, nullable=True)  # S3 bucket name
    object_key = Column(String, nullable=True)  # S3 object key
    mime_type = Column(String, nullable=True)  # MIME type (e.g., image/png, image/jpeg)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def price_usd(self):
        """Compute price_usd from price_minor"""
        if self.price_minor is not None:
            return self.price_minor / 100.0
        return None


# =================================
#  User Gem Purchases
# =================================
class UserGemPurchase(Base):
    __tablename__ = "user_gem_purchases"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    package_id = Column(Integer, ForeignKey("gem_package_config.id"), nullable=False)
    purchase_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    price_paid = Column(Float, nullable=False)
    gems_received = Column(Integer, nullable=False)

    # Relationships
    user = relationship("User", backref="gem_purchases")
    package = relationship("GemPackageConfig", backref="purchases")


# =================================
#  SubscriptionPlan Table
# =================================
class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    price_usd = Column(
        Float, nullable=False
    )  # Deprecated: use unit_amount_minor instead
    billing_interval = Column(String, nullable=False)  # 'month' or 'year'
    features = Column(String, nullable=True)  # JSON string of features
    stripe_price_id = Column(
        String, nullable=True, unique=True
    )  # Stripe price ID (source of truth)
    unit_amount_minor = Column(
        BigInteger, nullable=True
    )  # Price in minor units (cents)
    currency = Column(String, nullable=True)  # Currency code (e.g., 'usd')
    interval = Column(String, nullable=True)  # 'day', 'week', 'month', 'year'
    interval_count = Column(Integer, default=1)  # Number of intervals
    trial_period_days = Column(Integer, nullable=True)  # Trial period in days
    tax_behavior = Column(
        String, nullable=True
    )  # 'inclusive', 'exclusive', 'unspecified'
    livemode = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


# =================================
#  UserSubscription Table
# =================================
class UserSubscription(Base):
    __tablename__ = "user_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id"), nullable=False)
    stripe_subscription_id = Column(String, nullable=True, unique=True)
    status = Column(String, nullable=False)  # 'active', 'canceled', 'past_due', etc.
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    cancel_at_period_end = Column(Boolean, default=False)
    payment_method_id = Column(String, nullable=True)  # Stripe payment method ID
    stripe_customer_id = Column(String, nullable=True)
    latest_invoice_id = Column(String, nullable=True)
    default_payment_method_id = Column(String, nullable=True)
    pending_setup_intent_id = Column(String, nullable=True)
    cancel_at = Column(DateTime, nullable=True)
    canceled_at = Column(DateTime, nullable=True)
    pause_collection = Column(
        String, nullable=True
    )  # 'keep_as_draft', 'mark_uncollectible', 'void'
    livemode = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    user = relationship("User", back_populates="subscriptions")
    plan = relationship("SubscriptionPlan", backref="subscribers")

    __table_args__ = (
        Index(
            "ix_user_subscriptions_user_status_end",
            "user_id",
            "status",
            "current_period_end",
        ),
        Index("ix_user_subscriptions_status_end", "status", "current_period_end"),
    )


# =================================
#  Company Revenue Table (Monthly)
# =================================
class CompanyRevenue(Base):
    __tablename__ = "company_revenue"

    id = Column(Integer, primary_key=True, index=True)
    month_start_date = Column(
        Date, nullable=False, unique=True
    )  # First day of the month
    revenue_amount = Column(Float, nullable=False)
    subscriber_count = Column(
        Integer, nullable=False
    )  # Number of subscribers that month
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


# =================================
#  Blocks Table
# =================================
class Block(Base):
    __tablename__ = "blocks"

    id = Column(Integer, primary_key=True, index=True)
    blocker_id = Column(
        BigInteger, ForeignKey("users.account_id"), nullable=False, index=True
    )
    blocked_id = Column(
        BigInteger, ForeignKey("users.account_id"), nullable=False, index=True
    )
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    blocker = relationship("User", foreign_keys=[blocker_id], backref="blocked_users")
    blocked = relationship(
        "User", foreign_keys=[blocked_id], backref="blocked_by_users"
    )

    __table_args__ = (
        UniqueConstraint("blocker_id", "blocked_id", name="uq_blocks_blocker_blocked"),
    )


# =================================
#  Groups Tables (REMOVED - unused z_ tables)
# =================================
# All Group, Status, E2EE, and DM model classes have been removed
# as they are unused z_ tables


# =================================
#  User Presence Table
# =================================
class UserPresence(Base):
    __tablename__ = "user_presence"

    user_id = Column(
        BigInteger, ForeignKey("users.account_id"), nullable=False, primary_key=True
    )
    last_seen_at = Column(DateTime, nullable=True)
    device_online = Column(Boolean, nullable=False, default=False)
    privacy_settings = Column(
        MutableDict.as_mutable(JSONB), nullable=True
    )  # {share_last_seen, share_online, read_receipts}

    # Relationships
    user = relationship("User", backref="presence", uselist=False)


# =================================
#  Stripe Webhook Events Table
# =================================
class StripeWebhookEvent(Base):
    __tablename__ = "stripe_webhook_events"

    event_id = Column(String, primary_key=True)
    type = Column(String, nullable=False)
    livemode = Column(Boolean, nullable=False)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)
    status = Column(
        String, nullable=False, default="received"
    )  # received/processed/failed
    last_error = Column(String, nullable=True)


# =================================
#  Stripe Reconciliation Snapshots Table
# =================================
class StripeReconciliationSnapshot(Base):
    __tablename__ = "stripe_reconciliation_snapshots"

    id = Column(BigInteger, primary_key=True, index=True)
    as_of_date = Column(Date, nullable=False)
    currency = Column(String, nullable=False)
    platform_available_minor = Column(BigInteger, nullable=False)
    platform_pending_minor = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "as_of_date", "currency", name="uq_reconciliation_date_currency"
        ),
    )


# =================================
#  New Chat System Enums
# =================================
class PrivateChatStatus(PyEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class MessageStatus(PyEnum):
    SENT = "sent"
    DELIVERED = "delivered"
    SEEN = "seen"


# =================================
#  Global Chat Messages Table
# =================================
class GlobalChatMessage(Base):
    __tablename__ = "global_chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        BigInteger, ForeignKey("users.account_id"), nullable=False, index=True
    )
    message = Column(String, nullable=False)
    message_type = Column(String, default="text")  # "text", "system"
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    client_message_id = Column(String, nullable=True)  # For idempotency
    reply_to_message_id = Column(
        Integer, ForeignKey("global_chat_messages.id"), nullable=True, index=True
    )  # Reply to another message

    # Relationships
    user = relationship("User", backref="global_chat_messages")
    reply_to_message = relationship(
        "GlobalChatMessage", remote_side=[id], backref="replies"
    )

    __table_args__ = (
        # Unique constraint for idempotency (only when client_message_id is provided)
        # Note: PostgreSQL partial unique index will be created in migration
        Index("ix_global_chat_messages_user_id_created_at", "user_id", "created_at"),
    )


# =================================
#  Private Chat Conversations Table
# =================================
class PrivateChatConversation(Base):
    __tablename__ = "private_chat_conversations"

    id = Column(Integer, primary_key=True, index=True)
    user1_id = Column(
        BigInteger, ForeignKey("users.account_id"), nullable=False, index=True
    )
    user2_id = Column(
        BigInteger, ForeignKey("users.account_id"), nullable=False, index=True
    )
    status = Column(
        PG_ENUM(
            "pending",
            "accepted",
            "rejected",
            name="privatechatstatus",
            create_type=False,
        ),
        nullable=False,
        default="pending",
    )
    requested_by = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    responded_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_message_at = Column(DateTime, nullable=True, index=True)
    last_read_message_id_user1 = Column(
        Integer, nullable=True
    )  # Last message ID read by user1
    last_read_message_id_user2 = Column(
        Integer, nullable=True
    )  # Last message ID read by user2

    # Relationships
    user1 = relationship(
        "User", foreign_keys=[user1_id], backref="private_conversations_as_user1"
    )
    user2 = relationship(
        "User", foreign_keys=[user2_id], backref="private_conversations_as_user2"
    )
    requester = relationship("User", foreign_keys=[requested_by])

    __table_args__ = (
        UniqueConstraint("user1_id", "user2_id", name="uq_private_chat_users"),
    )


# =================================
#  Private Chat Messages Table
# =================================
class PrivateChatMessage(Base):
    __tablename__ = "private_chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(
        Integer, ForeignKey("private_chat_conversations.id"), nullable=False, index=True
    )
    sender_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    message = Column(String, nullable=False)
    status = Column(
        PG_ENUM("sent", "delivered", "seen", name="messagestatus", create_type=False),
        nullable=False,
        default="sent",
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    delivered_at = Column(DateTime, nullable=True)
    client_message_id = Column(String, nullable=True)  # For idempotency
    reply_to_message_id = Column(
        Integer, ForeignKey("private_chat_messages.id"), nullable=True, index=True
    )  # Reply to another message

    # Relationships
    conversation = relationship("PrivateChatConversation", backref="messages")
    sender = relationship("User", backref="private_chat_messages_sent")
    reply_to_message = relationship(
        "PrivateChatMessage", remote_side=[id], backref="replies"
    )

    __table_args__ = (
        # Unique constraint for idempotency (only when client_message_id is provided)
        # Note: PostgreSQL partial unique index will be created in migration
    )


# =================================
#  Trivia Live Chat Messages Table
# =================================
class TriviaLiveChatMessage(Base):
    __tablename__ = "trivia_live_chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    message = Column(String, nullable=False)
    draw_date = Column(
        Date, nullable=False, index=True
    )  # Use Date instead of DateTime for stability
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    client_message_id = Column(String, nullable=True)  # For idempotency
    reply_to_message_id = Column(
        Integer, ForeignKey("trivia_live_chat_messages.id"), nullable=True, index=True
    )  # Reply to another message

    # Relationships
    user = relationship("User", backref="trivia_live_chat_messages")
    reply_to_message = relationship(
        "TriviaLiveChatMessage", remote_side=[id], backref="replies"
    )


# =================================
#  Global Chat Viewers Table
# =================================
class GlobalChatViewer(Base):
    __tablename__ = "global_chat_viewers"

    user_id = Column(
        BigInteger, ForeignKey("users.account_id"), nullable=False, primary_key=True
    )
    last_seen = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    user = relationship("User", backref="global_chat_viewer")


# =================================
#  Trivia Live Chat Viewers Table
# =================================
class TriviaLiveChatViewer(Base):
    __tablename__ = "trivia_live_chat_viewers"

    user_id = Column(
        BigInteger, ForeignKey("users.account_id"), nullable=False, primary_key=True
    )
    draw_date = Column(
        Date, nullable=False, primary_key=True, index=True
    )  # Composite primary key
    last_seen = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    user = relationship("User", backref="trivia_live_chat_viewers")


# =================================
#  Trivia Live Chat Likes Table
# =================================
class TriviaLiveChatLike(Base):
    __tablename__ = "trivia_live_chat_likes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        BigInteger, ForeignKey("users.account_id"), nullable=False, index=True
    )
    draw_date = Column(
        Date, nullable=False, index=True
    )  # Like for a specific draw date
    message_id = Column(
        Integer, ForeignKey("trivia_live_chat_messages.id"), nullable=True
    )  # Null for session-level likes
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", backref="trivia_live_chat_likes")
    message = relationship("TriviaLiveChatMessage", backref="likes")

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "draw_date",
            "message_id",
            name="uq_trivia_live_chat_like_user_draw_message",
        ),
    )


# =================================
#  OneSignal Players Table
# =================================
class OneSignalPlayer(Base):
    __tablename__ = "onesignal_players"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        BigInteger, ForeignKey("users.account_id"), nullable=False, index=True
    )
    player_id = Column(String, unique=True, nullable=False, index=True)
    platform = Column(String, nullable=False)  # "ios", "android", "web"
    is_valid = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_active = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_failure_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", backref="onesignal_players")


# =================================
#  Notifications Table
# =================================
class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        BigInteger, ForeignKey("users.account_id"), nullable=False, index=True
    )
    title = Column(String, nullable=False)  # Notification title/heading
    body = Column(Text, nullable=False)  # Notification body/content
    type = Column(
        String, nullable=False
    )  # "chat_global", "chat_private", "chat_trivia_live", "system", "reward", etc.
    data = Column(
        JSONB, nullable=True
    )  # Additional data (e.g., message_id, conversation_id, etc.)
    read = Column(Boolean, default=False, nullable=False, index=True)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    user = relationship("User", backref="notifications")

    __table_args__ = (
        Index("ix_notifications_user_read_created", "user_id", "read", "created_at"),
    )


class UserDeviceVersion(Base):
    __tablename__ = "user_device_versions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        BigInteger, ForeignKey("users.account_id"), nullable=False, index=True
    )
    device_uuid = Column(String, nullable=False, index=True)
    device_name = Column(String, nullable=True)
    app_version = Column(String, nullable=False)
    os = Column(String, nullable=False)
    is_latest = Column(Boolean, default=True, nullable=False)
    reported_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user = relationship("User", back_populates="device_versions")

    __table_args__ = (
        UniqueConstraint("user_id", "device_uuid", name="uq_user_device_version"),
    )


class AppVersion(Base):
    __tablename__ = "app_versions"

    id = Column(Integer, primary_key=True, index=True)
    os = Column(String, nullable=False, unique=True, index=True)
    latest_version = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


# =================================
#  Wallet Transaction Table
# =================================
class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    amount_minor = Column(
        BigInteger, nullable=False
    )  # Amount in minor units (can be negative)
    currency = Column(String, nullable=False)
    kind = Column(
        String, nullable=False
    )  # deposit, withdraw, refund, fee, adjustment, etc.
    external_ref_type = Column(
        String, nullable=True
    )  # payment_intent, charge, refund, payout, etc.
    external_ref_id = Column(String, nullable=True)
    event_id = Column(String, nullable=True)
    idempotency_key = Column(String, nullable=True)
    livemode = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="wallet_transactions")


# =================================
#  Withdrawal Request Table
# =================================
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


# =================================
#  IAP Receipt Table
# =================================
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


# =================================
#  Chat Mute Preferences Table
# =================================
class ChatMutePreferences(Base):
    __tablename__ = "chat_mute_preferences"

    user_id = Column(
        BigInteger, ForeignKey("users.account_id"), primary_key=True, nullable=False
    )
    global_chat_muted = Column(Boolean, default=False, nullable=False)
    trivia_live_chat_muted = Column(Boolean, default=False, nullable=False)
    private_chat_muted_users = Column(
        JSONB, nullable=True
    )  # List of user IDs: [123, 456, ...]
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    user = relationship("User", backref="chat_mute_preferences", uselist=False)


# =================================
#  Trivia Mode Configuration Table
# =================================
class TriviaModeConfig(Base):
    __tablename__ = "trivia_mode_config"

    mode_id = Column(String, primary_key=True, index=True)
    mode_name = Column(String, nullable=False)
    questions_count = Column(Integer, nullable=False)
    reward_distribution = Column(Text, nullable=False)  # JSON string
    amount = Column(Float, default=0.0, nullable=False)
    # Fraction of gross subscription revenue allocated to the mode's prize pool (e.g. 0.005 = 0.5%).
    prize_pool_share = Column(Float, default=0.005, nullable=False)
    leaderboard_types = Column(Text, nullable=False)  # JSON array string
    ad_config = Column(Text, nullable=True)  # JSON string
    survey_config = Column(Text, nullable=True)  # JSON string
    # Badge fields (merged from badges table)
    badge_image_url = Column(String, nullable=True)  # Public S3 URL to the badge image
    badge_description = Column(String, nullable=True)  # Description of the badge
    badge_level = Column(
        Integer, nullable=True
    )  # Numeric level (for ordering, e.g., 1 for bronze, 2 for silver)
    badge_product_id = Column(
        String(5), unique=True, nullable=True
    )  # Unique product ID (e.g., BD001)
    badge_price_minor = Column(
        BigInteger, nullable=True
    )  # Price in minor units (cents)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    @property
    def badge_price_usd(self):
        """Compute badge_price_usd from badge_price_minor"""
        if self.badge_price_minor is not None:
            return self.badge_price_minor / 100.0
        return None


# =================================
#  Free Mode Questions Table
# =================================
class TriviaQuestionsFreeMode(Base):
    __tablename__ = "trivia_questions_free_mode"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(String, nullable=False)
    option_a = Column(String, nullable=False)
    option_b = Column(String, nullable=False)
    option_c = Column(String, nullable=False)
    option_d = Column(String, nullable=False)
    correct_answer = Column(String, nullable=False)
    fill_in_answer = Column(String, nullable=True)
    hint = Column(String, nullable=True)
    explanation = Column(String, nullable=True)
    category = Column(String, nullable=False)
    country = Column(String, nullable=True)
    difficulty_level = Column(String, nullable=False)
    picture_url = Column(String, nullable=True)
    question_hash = Column(
        String, index=True, nullable=False
    )  # MD5 hash for deduplication
    created_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("question_hash", name="uq_free_mode_question_hash"),
    )


# =================================
#  Free Mode Daily Questions Pool
# =================================
class TriviaQuestionsFreeModeDaily(Base):
    __tablename__ = "trivia_questions_free_mode_daily"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, nullable=False, index=True)
    question_id = Column(
        Integer, ForeignKey("trivia_questions_free_mode.id"), nullable=False
    )
    question_order = Column(Integer, nullable=False)  # 1-3
    is_used = Column(Boolean, default=False, nullable=False)

    # Relationships
    question = relationship("TriviaQuestionsFreeMode", backref="daily_allocations")
    __table_args__ = (
        UniqueConstraint(
            "date", "question_order", name="uq_free_mode_daily_question_order"
        ),
        UniqueConstraint("date", "question_id", name="uq_free_mode_daily_question_id"),
    )


# =================================
#  User Free Mode Daily Attempts
# =================================
class TriviaUserFreeModeDaily(Base):
    __tablename__ = "trivia_user_free_mode_daily"

    account_id = Column(BigInteger, ForeignKey("users.account_id"), primary_key=True)
    date = Column(Date, primary_key=True, nullable=False, index=True)
    question_order = Column(Integer, primary_key=True, nullable=False)  # 1-3

    question_id = Column(
        Integer, ForeignKey("trivia_questions_free_mode.id"), nullable=False
    )
    user_answer = Column(String, nullable=True)
    is_correct = Column(Boolean, nullable=True)
    answered_at = Column(DateTime, nullable=True)
    status = Column(
        String, nullable=False, default="locked"
    )  # locked, viewed, answered_wrong, answered_correct
    third_question_completed_at = Column(DateTime, nullable=True)  # For ranking winners

    # Relationships
    user = relationship("User", backref="free_mode_daily_attempts")
    question = relationship("TriviaQuestionsFreeMode", backref="user_attempts")
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "date",
            "question_order",
            name="uq_user_free_mode_daily_question",
        ),
    )


# =================================
#  Free Mode Winners
# =================================
class TriviaFreeModeWinners(Base):
    __tablename__ = "trivia_free_mode_winners"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    draw_date = Column(Date, nullable=False)
    position = Column(Integer, nullable=False)
    gems_awarded = Column(Integer, nullable=False)
    double_gems_flag = Column(Boolean, default=False, nullable=False)
    final_gems = Column(Integer, nullable=True)
    completed_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", backref="free_mode_wins")


# =================================
#  Free Mode Leaderboard
# =================================
class TriviaFreeModeLeaderboard(Base):
    __tablename__ = "trivia_free_mode_leaderboard"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    draw_date = Column(Date, nullable=False, index=True)
    position = Column(Integer, nullable=False)
    gems_awarded = Column(Integer, nullable=False)
    completed_at = Column(DateTime, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    user = relationship("User", backref="free_mode_leaderboard_entries")


# =================================
#  Bronze Mode ($5) Questions Table
# =================================
class TriviaQuestionsBronzeMode(Base):
    __tablename__ = "trivia_questions_bronze_mode"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(String, nullable=False)
    option_a = Column(String, nullable=False)
    option_b = Column(String, nullable=False)
    option_c = Column(String, nullable=False)
    option_d = Column(String, nullable=False)
    correct_answer = Column(String, nullable=False)
    fill_in_answer = Column(String, nullable=True)
    hint = Column(String, nullable=True)
    explanation = Column(String, nullable=True)
    category = Column(String, nullable=False)
    country = Column(String, nullable=True)
    difficulty_level = Column(String, nullable=False)
    picture_url = Column(String, nullable=True)
    question_hash = Column(
        String, index=True, nullable=False
    )  # MD5 hash for deduplication
    created_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("question_hash", name="uq_bronze_mode_question_hash"),
    )


# =================================
#  Bronze Mode ($5) Daily Questions Pool
# =================================
class TriviaQuestionsBronzeModeDaily(Base):
    __tablename__ = "trivia_questions_bronze_mode_daily"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, nullable=False, index=True)
    question_id = Column(
        Integer, ForeignKey("trivia_questions_bronze_mode.id"), nullable=False
    )
    question_order = Column(Integer, nullable=False)  # Always 1 for bronze mode
    is_used = Column(Boolean, default=False, nullable=False)

    # Relationships
    question = relationship("TriviaQuestionsBronzeMode", backref="daily_allocations")
    __table_args__ = (
        UniqueConstraint(
            "date", "question_order", name="uq_bronze_mode_daily_question_order"
        ),
        UniqueConstraint(
            "date", "question_id", name="uq_bronze_mode_daily_question_id"
        ),
    )


# =================================
#  User Bronze Mode ($5) Daily Attempts
# =================================
class TriviaUserBronzeModeDaily(Base):
    __tablename__ = "trivia_user_bronze_mode_daily"

    account_id = Column(BigInteger, ForeignKey("users.account_id"), primary_key=True)
    date = Column(Date, primary_key=True, nullable=False, index=True)

    question_id = Column(
        Integer, ForeignKey("trivia_questions_bronze_mode.id"), nullable=False
    )
    user_answer = Column(String, nullable=True)
    is_correct = Column(Boolean, nullable=True)
    submitted_at = Column(DateTime, nullable=True)  # Submission time for ranking
    status = Column(
        String, nullable=False, default="locked"
    )  # locked, viewed, answered

    # Relationships
    user = relationship("User", backref="bronze_mode_daily_attempts")
    question = relationship("TriviaQuestionsBronzeMode", backref="user_attempts")
    __table_args__ = (
        UniqueConstraint("account_id", "date", name="uq_user_bronze_mode_daily"),
    )


# =================================
#  Bronze Mode ($5) Winners
# =================================
class TriviaBronzeModeWinners(Base):
    __tablename__ = "trivia_bronze_mode_winners"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    draw_date = Column(Date, nullable=False)
    position = Column(Integer, nullable=False)
    money_awarded = Column(Float, nullable=False)  # Money in USD
    submitted_at = Column(DateTime, nullable=False)  # Submission time
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", backref="bronze_mode_wins")


# =================================
#  Bronze Mode ($5) Leaderboard
# =================================
class TriviaBronzeModeLeaderboard(Base):
    __tablename__ = "trivia_bronze_mode_leaderboard"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    draw_date = Column(Date, nullable=False, index=True)
    position = Column(Integer, nullable=False)
    money_awarded = Column(Float, nullable=False)  # Money in USD
    submitted_at = Column(DateTime, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    user = relationship("User", backref="bronze_mode_leaderboard_entries")


# =================================
#  Silver Mode ($10) Questions Table
# =================================
class TriviaQuestionsSilverMode(Base):
    __tablename__ = "trivia_questions_silver_mode"

    id = Column(Integer, primary_key=True, index=True)
    question = Column(String, nullable=False)
    option_a = Column(String, nullable=False)
    option_b = Column(String, nullable=False)
    option_c = Column(String, nullable=False)
    option_d = Column(String, nullable=False)
    correct_answer = Column(String, nullable=False)
    fill_in_answer = Column(String, nullable=True)
    hint = Column(String, nullable=True)
    explanation = Column(String, nullable=True)
    category = Column(String, nullable=False)
    country = Column(String, nullable=True)
    difficulty_level = Column(String, nullable=False)
    picture_url = Column(String, nullable=True)
    question_hash = Column(
        String, index=True, nullable=False
    )  # MD5 hash for deduplication
    created_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("question_hash", name="uq_silver_mode_question_hash"),
    )


# =================================
#  Silver Mode ($10) Daily Questions Pool
# =================================
class TriviaQuestionsSilverModeDaily(Base):
    __tablename__ = "trivia_questions_silver_mode_daily"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, nullable=False, index=True)
    question_id = Column(
        Integer, ForeignKey("trivia_questions_silver_mode.id"), nullable=False
    )
    question_order = Column(Integer, nullable=False)  # Always 1 for silver mode
    is_used = Column(Boolean, default=False, nullable=False)

    # Relationships
    question = relationship("TriviaQuestionsSilverMode", backref="daily_allocations")
    __table_args__ = (
        UniqueConstraint(
            "date", "question_order", name="uq_silver_mode_daily_question_order"
        ),
        UniqueConstraint(
            "date", "question_id", name="uq_silver_mode_daily_question_id"
        ),
    )


# =================================
#  User Silver Mode ($10) Daily Attempts
# =================================
class TriviaUserSilverModeDaily(Base):
    __tablename__ = "trivia_user_silver_mode_daily"

    account_id = Column(BigInteger, ForeignKey("users.account_id"), primary_key=True)
    date = Column(Date, primary_key=True, nullable=False)

    question_id = Column(
        Integer, ForeignKey("trivia_questions_silver_mode.id"), nullable=False
    )
    user_answer = Column(String, nullable=True)
    is_correct = Column(Boolean, nullable=True)
    submitted_at = Column(DateTime, nullable=True)  # Submission time for ranking
    status = Column(
        String, nullable=False, default="locked"
    )  # locked, viewed, answered

    # Relationships
    user = relationship("User", backref="silver_mode_daily_attempts")
    question = relationship("TriviaQuestionsSilverMode", backref="user_attempts")
    __table_args__ = (
        UniqueConstraint("account_id", "date", name="uq_user_silver_mode_daily"),
    )


# =================================
#  Silver Mode ($10) Winners
# =================================
class TriviaSilverModeWinners(Base):
    __tablename__ = "trivia_silver_mode_winners"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    draw_date = Column(Date, nullable=False)
    position = Column(Integer, nullable=False)
    money_awarded = Column(Float, nullable=False)  # Money in USD
    submitted_at = Column(DateTime, nullable=False)  # Submission time
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", backref="silver_mode_wins")


# =================================
#  Silver Mode ($10) Leaderboard
# =================================
class TriviaSilverModeLeaderboard(Base):
    __tablename__ = "trivia_silver_mode_leaderboard"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    draw_date = Column(Date, nullable=False, index=True)
    position = Column(Integer, nullable=False)
    money_awarded = Column(Float, nullable=False)  # Money in USD
    submitted_at = Column(DateTime, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    user = relationship("User", backref="silver_mode_leaderboard_entries")
