from sqlalchemy import (
    Column, Integer, String, Float, Boolean, ForeignKey, DateTime, BigInteger, Date, UniqueConstraint, Text, Enum as SQLEnum, LargeBinary
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
from enum import Enum as PyEnum
from sqlalchemy.orm import relationship
from db import Base
from datetime import datetime, date
import random
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

def generate_account_id():
    """Generate a 10-digit random unique number."""
    return int("".join(str(random.randint(0, 9)) for _ in range(10)))

# =================================
#  Users Table
# =================================
class User(Base):
    __tablename__ = "users"

    account_id = Column(BigInteger, primary_key=True, unique=True, index=True, nullable=False, default=generate_account_id)
    descope_user_id = Column(String, unique=True, index=True, nullable=True)
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
    is_admin = Column(Boolean, default=False)  # Added is_admin field

    subscriber_number = Column(String, nullable=True)
    username_updated = Column(Boolean, default=False)  # Track if username has been updated before
    subscription_flag = Column(Boolean, default=False)
    sign_up_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Added fields for trivia game
    gems = Column(Integer, default=0)  # Track user's gems
    streaks = Column(Integer, default=0)  # Track user's streaks
    lifeline_changes_remaining = Column(Integer, default=3)  # Track remaining question changes
    last_streak_date = Column(DateTime, nullable=True)  # To track daily streaks
    
    # Daily draw eligibility tracking
    daily_eligibility_flag = Column(Boolean, default=False)  # True if user answered all 3 questions correctly today

    # Badge fields
    badge_id = Column(String, ForeignKey("badges.id"), nullable=True)  # Reference to badge ID

    # Wallet fields
    wallet_balance = Column(Float, default=0.0)  # User's wallet balance
    total_spent = Column(Float, default=0.0)  # Total amount spent in the app
    last_wallet_update = Column(DateTime, nullable=True)  # Last time wallet was updated

    # Stripe integration fields
    stripe_customer_id = Column(String, nullable=True, index=True)  # Stripe customer ID for payment methods

    # Store purchased items
    owned_boosts = Column(Text, nullable=True)  # JSON string of owned boost items
    
    # Cosmetic selections
    selected_avatar_id = Column(String, nullable=True)  # Currently selected avatar ID
    selected_frame_id = Column(String, nullable=True)  # Currently selected frame ID

    # Relationships
    entries = relationship("TriviaQuestionsEntries", back_populates="user")
    payments = relationship("Payment", back_populates="user")
    badge_info = relationship("Badge", back_populates="users")
    payment_transactions = relationship("PaymentTransaction", back_populates="user")
    bank_accounts = relationship("UserBankAccount", back_populates="user")
    subscriptions = relationship("UserSubscription", back_populates="user")
    live_chat_messages = relationship("LiveChatMessage", back_populates="user")
    # You could add a relationship for Comments, Chats, or Withdrawals if needed
    # (depending on whether they link to a user table).

# =================================
#  Entries Table
# =================================
class TriviaQuestionsEntries(Base):
    __tablename__ = "trivia_questions_entries"

    account_id = Column(BigInteger, ForeignKey("users.account_id"), primary_key=True)
    ques_attempted = Column(Integer, nullable=False)
    correct_answers = Column(Integer, nullable=False)
    wrong_answers = Column(Integer, nullable=False)
    date = Column(Date, default=datetime.utcnow().date(), primary_key=True, nullable=False)

    # Relationship
    user = relationship("User", back_populates="entries")


# =================================
#  Payment Table
# =================================
class Payment(Base):
    __tablename__ = "payment"

    account_id = Column(BigInteger, ForeignKey("users.account_id"), primary_key=True)
    bank_account_number = Column(String, nullable=False)
    routing_number = Column(String, nullable=False)
    card_number = Column(String, nullable=False)
    expiration_date = Column(String, nullable=False)
    cvv = Column(String, nullable=False)
    autopayment = Column(Boolean, default=False)

    first_name_on_card = Column(String, nullable=True)
    last_name_on_card = Column(String, nullable=True)

    # Additional billing fields from the diagram
    billing_street_1 = Column(String, nullable=True)
    billing_street_2 = Column(String, nullable=True)
    billing_suite_or_apt_num = Column(String, nullable=True)
    billing_city = Column(String, nullable=True)
    billing_state = Column(String, nullable=True)
    billing_country = Column(String, nullable=True)
    billing_zip = Column(String, nullable=True)

    # Payment tracking
    payment_history = Column(String, nullable=True)  # CSV of dates or JSON
    subscription_date = Column(DateTime, nullable=True)
    six_months_subscription = Column(Boolean, default=False)
    twelve_months_subscription = Column(Boolean, default=False)

    # Relationship
    user = relationship("User", back_populates="payments")


# =================================
#  Trivia Table
# =================================
class Trivia(Base):
    __tablename__ = "trivia"

    question_number = Column(Integer, primary_key=True)
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
    created_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    question_done = Column(Boolean, default=False)
    que_displayed_date = Column(DateTime, nullable=True)


# =================================
#  LiveUpdates Table
# =================================
class LiveUpdate(Base):
    __tablename__ = "liveupdates"

    id = Column(Integer, primary_key=True, index=True)
    video_url = Column(String, nullable=False)
    description = Column(String, nullable=True)
    share_text = Column(String, nullable=True)  # Text for sharing
    app_link = Column(String, nullable=True)    # App link for sharing
    created_date = Column(DateTime, default=datetime.utcnow, nullable=False)


# =================================
#  New: Updates Table
# =================================
class UpdatePost(Base):
    """
    Based on your first image:
    picture_url, post_id, post_date, description, likes, shares
    """
    __tablename__ = "updates"

    post_id = Column(Integer, primary_key=True, index=True)
    picture_url = Column(String, nullable=True)
    post_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    description = Column(String, nullable=True)
    likes = Column(Integer, default=0)
    shares = Column(Integer, default=0)


# =================================
#  New: Comments Table
# =================================
class Comment(Base):
    """
    post_id, account_id, comment, date, likes
    Possibly relationships to UpdatePost (post_id) and User (account_id).
    """
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("updates.post_id"), nullable=False)
    account_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    comment = Column(String, nullable=False)
    date = Column(DateTime, default=datetime.utcnow, nullable=False)
    likes = Column(Integer, default=0)

    # Relationships (optional, if you want them)
    post = relationship("UpdatePost", backref="comments")
    user = relationship("User", backref="comments")


# =================================
#  New: Chats Table
# =================================
class Chat(Base):
    """
    sender_account_id, receiver_account_id, message, message_id,
    sent_at, request_type, request_status, request_responded_at
    """
    __tablename__ = "chats"

    message_id = Column(Integer, primary_key=True, index=True)
    sender_account_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    receiver_account_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    message = Column(String, nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    request_type = Column(String, nullable=True)
    # request_status: 'pending', 'accepted', 'declined', 'blocked'
    request_status = Column(String, nullable=True)
    request_responded_at = Column(DateTime, nullable=True)

    # Optionally define relationships to user
    sender = relationship("User", foreign_keys=[sender_account_id], backref="sent_chats")
    receiver = relationship("User", foreign_keys=[receiver_account_id], backref="received_chats")


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
    withdrawal_status = Column(String, nullable=False)  # e.g. "requested", "completed", "failed"
    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)

    # Relationship to user if desired
    user = relationship("User", backref="withdrawals")

# =================================
#  Daily Questions Table
# =================================
class TriviaQuestionsDaily(Base):
    """Shared daily questions pool (0-4 questions per day for all users)"""
    __tablename__ = "trivia_questions_daily"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, default=datetime.utcnow, nullable=False)
    question_number = Column(Integer, ForeignKey("trivia.question_number"), nullable=False)
    question_order = Column(Integer, nullable=False)  # 1-4 for ordering
    is_common = Column(Boolean, default=False)  # True for first question (free for all)
    is_used = Column(Boolean, default=False)  # True if ANY user has viewed/unlocked this question
    
    # Relationships
    question = relationship("Trivia", backref="daily_allocations")
    __table_args__ = (
        UniqueConstraint('date', 'question_order', name='uq_daily_question_order'),
        UniqueConstraint('date', 'question_number', name='uq_daily_question_number'),
    )

# =================================
#  User Daily Questions Table (Unlocks + Attempts)
# =================================
class UnlockMethod(PyEnum):
    FREE = 'free'
    GEMS = 'gems'
    USD = 'usd'

class QuestionStatus(PyEnum):
    LOCKED = 'locked'
    VIEWED = 'viewed'
    ANSWERED_WRONG = 'answered_wrong'
    ANSWERED_CORRECT = 'answered_correct'
    SKIPPED = 'skipped'

class TriviaUserDaily(Base):
    """Per-user, per-day, per-question unlocks and attempts"""
    __tablename__ = "trivia_user_daily"

    account_id = Column(BigInteger, ForeignKey("users.account_id"), primary_key=True)
    date = Column(Date, primary_key=True, nullable=False)
    question_order = Column(Integer, primary_key=True, nullable=False)  # 1-4
    
    question_number = Column(Integer, ForeignKey("trivia.question_number"), nullable=False)
    unlock_method = Column(String, nullable=True)  # 'free', 'gems', 'usd' - NULL = not unlocked
    viewed_at = Column(DateTime, nullable=True)  # When user unlocked/viewed
    user_answer = Column(String, nullable=True)  # User's submitted answer
    is_correct = Column(Boolean, nullable=True)  # Whether answer was correct
    answered_at = Column(DateTime, nullable=True)  # When user answered
    status = Column(String, nullable=False, default='locked')  # locked, viewed, answered_wrong, answered_correct, skipped
    retry_count = Column(Integer, default=0, nullable=False)
    
    # Relationships
    user = relationship("User", backref="daily_user_questions")
    question = relationship("Trivia", backref="user_daily_attempts")
    __table_args__ = (
        UniqueConstraint('account_id', 'date', 'question_order', name='uq_user_daily_question'),
    )

# =================================
#  Daily Login Rewards Table
# =================================
class UserDailyRewards(Base):
    """Per-user weekly daily login rewards tracking (Monday-Sunday)"""
    __tablename__ = "user_daily_rewards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False, index=True)
    week_start_date = Column(Date, nullable=False)  # Monday of the week
    day1_status = Column(Boolean, default=False, nullable=False)  # Monday
    day2_status = Column(Boolean, default=False, nullable=False)  # Tuesday
    day3_status = Column(Boolean, default=False, nullable=False)  # Wednesday
    day4_status = Column(Boolean, default=False, nullable=False)  # Thursday
    day5_status = Column(Boolean, default=False, nullable=False)  # Friday
    day6_status = Column(Boolean, default=False, nullable=False)  # Saturday
    day7_status = Column(Boolean, default=False, nullable=False)  # Sunday
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    user = relationship("User", backref="daily_rewards")
    __table_args__ = (
        UniqueConstraint('account_id', 'week_start_date', name='uq_user_week_rewards'),
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
    price_gems = Column(Integer, nullable=True)  # Price in gems (if purchasable with gems)
    price_usd = Column(Float, nullable=True)  # Price in USD (if purchasable with real money)
    is_premium = Column(Boolean, default=False)  # Whether it's a premium avatar
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)  # When the avatar was added
    
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
    price_gems = Column(Integer, nullable=True)  # Price in gems (if purchasable with gems)
    price_usd = Column(Float, nullable=True)  # Price in USD (if purchasable with real money)
    is_premium = Column(Boolean, default=False)  # Whether it's a premium frame
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)  # When the frame was added
    
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
        UniqueConstraint('user_id', 'avatar_id', name='uq_user_avatar'),
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
        UniqueConstraint('user_id', 'frame_id', name='uq_user_frame'),
    )

# =================================
#  Badge Table
# =================================
class Badge(Base):
    """
    Badge model for user achievement badges.
    
    Note: image_url should contain a PUBLIC S3 URL (not presigned).
    Badges are shared assets (only 4 total), so they should be publicly accessible
    to avoid unnecessary presigned URL generation and expiration.
    
    Example URL format: https://triviapay-assets.s3.us-east-2.amazonaws.com/badges/bronze.png
    """
    __tablename__ = "badges"
    
    id = Column(String, primary_key=True, index=True)  # Unique ID for the badge (e.g., "bronze", "silver", "gold")
    name = Column(String, nullable=False)  # Display name
    description = Column(String, nullable=True)  # Description of the badge
    image_url = Column(String, nullable=False)  # Public S3 URL to the badge image (not presigned)
    level = Column(Integer, nullable=False)  # Numeric level (for ordering, e.g., 1 for bronze, 2 for silver)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)  # When the badge was added
    
    # Relationships
    users = relationship("User", back_populates="badge_info")

# =================================
#  Trivia Draw Configuration
# =================================
class TriviaDrawConfig(Base):
    __tablename__ = "winners_draw_config"
    
    id = Column(Integer, primary_key=True, index=True)
    is_custom = Column(Boolean, default=False)  # Whether using custom winner count
    custom_winner_count = Column(Integer, nullable=True)  # Custom number of winners
    custom_data = Column(String, nullable=True)  # JSON string for additional configuration
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# =================================
#  Trivia Draw Winners Table
# =================================
class TriviaQuestionsWinners(Base):
    __tablename__ = "winners_draw_results"
    
    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    prize_amount = Column(Float, nullable=False)
    position = Column(Integer, nullable=False)  # Winner position (1st, 2nd, etc.)
    draw_date = Column(Date, nullable=False)  # Date of the draw
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationship
    user = relationship("User", backref="trivia_draw_wins")

# =================================
#  Draw Configuration Table
# =================================

# =================================
#  Gem Package Configuration
# =================================
class GemPackageConfig(Base):
    __tablename__ = "gem_package_config"
    
    id = Column(Integer, primary_key=True, index=True)
    price_usd = Column(Float, nullable=False)
    gems_amount = Column(Integer, nullable=False)
    is_one_time = Column(Boolean, default=False)  # For one-time offers
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# =================================
#  Boost Configuration
# =================================
class BoostConfig(Base):
    __tablename__ = "boost_config"
    
    boost_type = Column(String, primary_key=True, index=True)  # e.g. "fifty_fifty", "hint", etc.
    gems_cost = Column(Integer, nullable=False)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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
#  Letters Table
# =================================
class Letter(Base):
    __tablename__ = "letters"
    
    letter = Column(String, primary_key=True)
    image_url = Column(String, nullable=False)

# =================================
#  Country Codes Table
# =================================
class CountryCode(Base):
    __tablename__ = "country_codes"
    
    # Create a composite primary key since some country codes (like +1) are shared by multiple countries
    code = Column(String, primary_key=True)  # Country calling code (e.g., +1, +44)
    country_iso = Column(String, primary_key=True)  # ISO code (e.g., US, GB)
    
    country_name = Column(String, nullable=False)
    flag_url = Column(String, nullable=True)  # URL to the country flag image
    created_at = Column(DateTime, default=datetime.utcnow)

# =================================
#  Payment Transaction Table
# =================================
class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    payment_intent_id = Column(String, unique=True, nullable=True, index=True)  # Changed to nullable for withdrawals without intent IDs
    amount = Column(Float, nullable=False)
    currency = Column(String, nullable=False)
    status = Column(String, nullable=False)  # 'succeeded', 'failed', 'processing', 'pending', etc.
    payment_method = Column(String, nullable=True)
    payment_method_type = Column(String, nullable=True)  # 'card', 'bank_transfer', 'standard', 'instant', etc.
    last_error = Column(String, nullable=True)
    payment_metadata = Column(String, nullable=True)  # JSON string of metadata
    admin_notes = Column(String, nullable=True)  # Notes added by admins during processing
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="payment_transactions")

# =================================
#  UserBankAccount Table
# =================================
class UserBankAccount(Base):
    __tablename__ = "user_bank_accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    account_name = Column(String, nullable=False)
    account_number_last4 = Column(String(4), nullable=False)  # Last 4 digits only for security
    account_number_encrypted = Column(String, nullable=True)  # Encrypted full account number
    routing_number_encrypted = Column(String, nullable=True)  # Encrypted routing number
    bank_name = Column(String, nullable=False)
    is_default = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    stripe_bank_account_id = Column(String, nullable=True)  # ID from Stripe for bank account
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationship - Changed from backref to back_populates
    user = relationship("User", back_populates="bank_accounts")

# =================================
#  SubscriptionPlan Table
# =================================
class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    price_usd = Column(Float, nullable=False)
    billing_interval = Column(String, nullable=False)  # 'month' or 'year'
    features = Column(String, nullable=True)  # JSON string of features
    stripe_price_id = Column(String, nullable=True)  # Stripe price ID
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

# =================================
#  UserSubscription Table
# =================================
class UserSubscription(Base):
    __tablename__ = "user_subscriptions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id"), nullable=False)
    stripe_subscription_id = Column(String, nullable=True)
    status = Column(String, nullable=False)  # 'active', 'canceled', 'past_due', etc.
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    cancel_at_period_end = Column(Boolean, default=False)
    payment_method_id = Column(String, nullable=True)  # Stripe payment method ID
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="subscriptions")
    plan = relationship("SubscriptionPlan", backref="subscribers")


# =================================
#  Company Revenue Table (Monthly)
# =================================
class CompanyRevenue(Base):
    __tablename__ = "company_revenue"
    
    id = Column(Integer, primary_key=True, index=True)
    month_start_date = Column(Date, nullable=False, unique=True)  # First day of the month
    revenue_amount = Column(Float, nullable=False)
    subscriber_count = Column(Integer, nullable=False)  # Number of subscribers that month
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

# =================================
#  Live Chat Session Table
# =================================
class LiveChatSession(Base):
    __tablename__ = "live_chat_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    session_name = Column(String, nullable=False)  # e.g., "Today's Winners Chat"
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)
    viewer_count = Column(Integer, default=0)
    total_likes = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships - using back_populates to match existing pattern
    messages = relationship("LiveChatMessage", back_populates="session")

# =================================
#  Live Chat Messages Table
# =================================
class LiveChatMessage(Base):
    __tablename__ = "live_chat_messages"
    
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("live_chat_sessions.id"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    message = Column(String, nullable=False)
    message_type = Column(String, default="text")  # "text", "system", "announcement"
    likes = Column(Integer, default=0)
    client_message_id = Column(String, nullable=True)  # Optional client-provided ID for idempotency
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships - using back_populates to match existing pattern
    session = relationship("LiveChatSession", back_populates="messages")
    user = relationship("User", back_populates="live_chat_messages")
    
    # Note: Unique constraint is created via migration script as a partial index
    # to allow NULL values while enforcing uniqueness when client_message_id is provided
    # __table_args__ = (
    #     UniqueConstraint('session_id', 'user_id', 'client_message_id', name='uq_client_message_id'),
    # )

# =================================
#  Live Chat Likes Table
# =================================
class LiveChatLike(Base):
    __tablename__ = "live_chat_likes"
    
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("live_chat_sessions.id"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    message_id = Column(Integer, ForeignKey("live_chat_messages.id"), nullable=True)  # Null for session likes
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships - using backref to match existing pattern
    session = relationship("LiveChatSession", backref="session_likes")
    user = relationship("User", backref="live_chat_likes")
    message = relationship("LiveChatMessage", backref="message_likes")

# =================================
#  Live Chat Viewers Table
# =================================
class LiveChatViewer(Base):
    __tablename__ = "live_chat_viewers"
    
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("live_chat_sessions.id"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    joined_at = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    
    # Relationships - using backref to match existing pattern
    session = relationship("LiveChatSession", backref="session_viewers")
    user = relationship("User", backref="live_chat_viewers")

# =================================
#  E2EE Devices Table
# =================================
class E2EEDevice(Base):
    __tablename__ = "e2ee_devices"
    
    device_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False, index=True)
    device_name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, nullable=False, default="active")  # 'active', 'revoked'
    
    # Relationships
    user = relationship("User", backref="e2ee_devices")
    key_bundle = relationship("E2EEKeyBundle", back_populates="device", uselist=False)
    one_time_prekeys = relationship("E2EEOneTimePrekey", back_populates="device")

# =================================
#  E2EE Key Bundles Table
# =================================
class E2EEKeyBundle(Base):
    __tablename__ = "e2ee_key_bundles"
    
    device_id = Column(UUID(as_uuid=True), ForeignKey("e2ee_devices.device_id"), primary_key=True, unique=True)
    identity_key_pub = Column(String, nullable=False)  # Base64 encoded
    signed_prekey_pub = Column(String, nullable=False)  # Base64 encoded
    signed_prekey_sig = Column(String, nullable=False)  # Base64 encoded signature
    prekeys_remaining = Column(Integer, nullable=False, default=0)
    bundle_version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    device = relationship("E2EEDevice", back_populates="key_bundle")

# =================================
#  E2EE One-Time Prekeys Table
# =================================
class E2EEOneTimePrekey(Base):
    __tablename__ = "e2ee_one_time_prekeys"
    
    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(UUID(as_uuid=True), ForeignKey("e2ee_devices.device_id"), nullable=False)
    prekey_pub = Column(String, nullable=False)  # Base64 encoded
    claimed = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    device = relationship("E2EEDevice", back_populates="one_time_prekeys")

# =================================
#  DM Conversations Table
# =================================
class DMConversation(Base):
    __tablename__ = "dm_conversations"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    last_message_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    sealed_sender_enabled = Column(Boolean, nullable=False, default=False)
    
    # Relationships
    participants = relationship("DMParticipant", back_populates="conversation")
    messages = relationship("DMMessage", back_populates="conversation")

# =================================
#  DM Participants Table
# =================================
class DMParticipant(Base):
    __tablename__ = "dm_participants"
    
    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("dm_conversations.id"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False, index=True)
    device_ids = Column(JSONB, nullable=True)  # Array of device UUIDs
    
    # Relationships
    conversation = relationship("DMConversation", back_populates="participants")
    user = relationship("User", backref="dm_participants")
    
    __table_args__ = (
        UniqueConstraint('conversation_id', 'user_id', name='uq_dm_participants_conversation_user'),
    )

# =================================
#  DM Messages Table
# =================================
class DMMessage(Base):
    __tablename__ = "dm_messages"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("dm_conversations.id"), nullable=False, index=True)
    sender_user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    sender_device_id = Column(UUID(as_uuid=True), ForeignKey("e2ee_devices.device_id"), nullable=False)
    ciphertext = Column(LargeBinary, nullable=False)  # Binary encrypted payload
    proto = Column(Integer, nullable=False)  # 1=DR message, 2=PreKey message
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    client_message_id = Column(String, unique=True, nullable=True)  # For idempotency
    
    # Relationships
    conversation = relationship("DMConversation", back_populates="messages")
    sender_user = relationship("User", foreign_keys=[sender_user_id], backref="dm_messages_sent")
    sender_device = relationship("E2EEDevice", foreign_keys=[sender_device_id])
    delivery_records = relationship("DMDelivery", back_populates="message")

# =================================
#  DM Delivery Table
# =================================
class DMDelivery(Base):
    __tablename__ = "dm_delivery"
    
    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(UUID(as_uuid=True), ForeignKey("dm_messages.id"), nullable=False)
    recipient_user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False, index=True)
    delivered_at = Column(DateTime, nullable=True)
    read_at = Column(DateTime, nullable=True, index=True)
    
    # Relationships
    message = relationship("DMMessage", back_populates="delivery_records")
    recipient_user = relationship("User", foreign_keys=[recipient_user_id], backref="dm_messages_received")
    
    __table_args__ = (
        UniqueConstraint('message_id', 'recipient_user_id', name='uq_dm_delivery_message_recipient'),
    )

# =================================
#  Blocks Table
# =================================
class Block(Base):
    __tablename__ = "blocks"
    
    id = Column(Integer, primary_key=True, index=True)
    blocker_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False, index=True)
    blocked_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    blocker = relationship("User", foreign_keys=[blocker_id], backref="blocked_users")
    blocked = relationship("User", foreign_keys=[blocked_id], backref="blocked_by_users")
    
    __table_args__ = (
        UniqueConstraint('blocker_id', 'blocked_id', name='uq_blocks_blocker_blocked'),
    )

# =================================
#  Device Revocations Table
# =================================
class DeviceRevocation(Base):
    __tablename__ = "device_revocations"
    
    user_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False, primary_key=True)
    device_id = Column(UUID(as_uuid=True), nullable=False, primary_key=True)
    revoked_at = Column(DateTime, default=datetime.utcnow)
    reason = Column(String, nullable=True)