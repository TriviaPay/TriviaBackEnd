from sqlalchemy import (
    Column, Integer, String, Float, Boolean, ForeignKey, DateTime, BigInteger, Date
)
from sqlalchemy.orm import relationship
from db import Base
from datetime import datetime, date
import random

# =================================
#  Users Table
# =================================
class User(Base):
    __tablename__ = "users"

    account_id = Column(BigInteger, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
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
    date_of_birth = Column(Date, nullable=True)
    referral_code = Column(String(5), unique=True, nullable=True)
    referred_by = Column(String(5), nullable=True)
    referral_count = Column(Integer, default=0)
    is_referred = Column(Boolean, default=False)

    subscriber_number = Column(String, nullable=True)
    username = Column(String, nullable=True, unique=True)
    subscription_flag = Column(Boolean, default=False)
    sign_up_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    refresh_token = Column(String, nullable=True)
    sub = Column(String, nullable=True, unique=True)  # Auth0 sub claim
    
    # Added fields for trivia game
    gems = Column(Integer, default=0)  # Track user's gems
    streaks = Column(Integer, default=0)  # Track user's streaks
    lifeline_changes_remaining = Column(Integer, default=3)  # Track remaining question changes
    last_streak_date = Column(DateTime, nullable=True)  # To track daily streaks

    # Badge fields
    badge_id = Column(String, ForeignKey("badges.id"), nullable=True)  # Reference to badge ID
    badge_image_url = Column(String, nullable=True)  # URL to badge image (cached for performance)

    # Wallet fields
    wallet_balance = Column(Float, default=0.0)  # User's wallet balance
    total_spent = Column(Float, default=0.0)  # Total amount spent in the app
    last_wallet_update = Column(DateTime, nullable=True)  # Last time wallet was updated

    # Store purchased items
    owned_cosmetics = Column(String, nullable=True)  # JSON string of owned cosmetic items
    owned_boosts = Column(String, nullable=True)  # JSON string of owned boost items
    
    # Cosmetic selections
    selected_avatar_id = Column(String, nullable=True)  # Currently selected avatar ID
    selected_frame_id = Column(String, nullable=True)  # Currently selected frame ID

    # Relationships
    winners = relationship("Winner", back_populates="user")
    entries = relationship("Entry", back_populates="user")
    payments = relationship("Payment", back_populates="user")
    daily_questions = relationship("DailyQuestion", back_populates="user")
    badge_info = relationship("Badge", back_populates="users")
    # You could add a relationship for Comments, Chats, or Withdrawals if needed
    # (depending on whether they link to a user table).

def generate_account_id():
    """Generate a 10-digit random unique number."""
    return int("".join(str(random.randint(0, 9)) for _ in range(10)))

# =================================
#  Entries Table
# =================================
class Entry(Base):
    __tablename__ = "entries"

    account_id = Column(BigInteger, ForeignKey("users.account_id"), primary_key=True)
    number_of_entries = Column(Integer, nullable=False)
    ques_attempted = Column(Integer, nullable=False)
    correct_answers = Column(Integer, nullable=False)
    wrong_answers = Column(Integer, nullable=False)
    date = Column(Date, default=datetime.utcnow().date(), primary_key=True, nullable=False)

    # Relationship
    user = relationship("User", back_populates="entries")


# =================================
#  Winners Table
# =================================
class Winner(Base):
    __tablename__ = "winners"

    account_id = Column(BigInteger, ForeignKey("users.account_id"), primary_key=True)
    amount_won = Column(Float, nullable=False)
    win_date = Column(DateTime, nullable=False)

    first_prize = Column(Float, nullable=True)
    second_prize = Column(Float, nullable=True)
    third_prize = Column(Float, nullable=True)
    fourth_prize = Column(Float, nullable=True)
    fifth_prize = Column(Float, nullable=True)
    sixth_prize = Column(Float, nullable=True)
    seventh_prize = Column(Float, nullable=True)
    eighth_prize = Column(Float, nullable=True)
    ninth_prize = Column(Float, nullable=True)
    tenth_prize = Column(Float, nullable=True)

    # Relationship
    user = relationship("User", back_populates="winners")


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
class DailyQuestion(Base):
    """Track which questions are allocated to users each day"""
    __tablename__ = "daily_questions"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(BigInteger, ForeignKey("users.account_id"), nullable=False)
    question_number = Column(Integer, ForeignKey("trivia.question_number"), nullable=False)
    date = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_common = Column(Boolean, default=False)  # True for first question
    question_order = Column(Integer, nullable=False)  # 1-4 for ordering
    is_used = Column(Boolean, default=False)  # Track if question was attempted
    was_changed = Column(Boolean, default=False)  # Track if question was changed via lifeline
    
    # New fields to track answers and correctness
    answer = Column(String, nullable=True)  # User's answer
    is_correct = Column(Boolean, nullable=True)  # Whether the answer was correct
    answered_at = Column(DateTime, nullable=True)  # When the answer was submitted
    
    # Relationships
    user = relationship("User", back_populates="daily_questions")
    question = relationship("Trivia", backref="daily_allocations")

# =================================
#  Cosmetics - Avatars Table
# =================================
class Avatar(Base):
    __tablename__ = "avatars"
    
    id = Column(String, primary_key=True, index=True)  # Unique ID for the avatar
    name = Column(String, nullable=False)  # Display name
    description = Column(String, nullable=True)  # Description of the avatar
    image_url = Column(String, nullable=False)  # URL to the avatar image
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
    image_url = Column(String, nullable=False)  # URL to the frame image
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

# =================================
#  Badge Table
# =================================
class Badge(Base):
    __tablename__ = "badges"
    
    id = Column(String, primary_key=True, index=True)  # Unique ID for the badge (e.g., "bronze", "silver", "gold")
    name = Column(String, nullable=False)  # Display name
    description = Column(String, nullable=True)  # Description of the badge
    image_url = Column(String, nullable=False)  # URL to the badge image
    level = Column(Integer, nullable=False)  # Numeric level (for ordering, e.g., 1 for bronze, 2 for silver)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)  # When the badge was added
    
    # Relationships
    users = relationship("User", back_populates="badge_info")

# =================================
#  Trivia Draw Configuration
# =================================
class TriviaDrawConfig(Base):
    __tablename__ = "trivia_draw_config"
    
    id = Column(Integer, primary_key=True, index=True)
    is_custom = Column(Boolean, default=False)  # Whether using custom winner count
    custom_winner_count = Column(Integer, nullable=True)  # Custom number of winners
    custom_data = Column(String, nullable=True)  # JSON string for additional configuration
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# =================================
#  Trivia Draw Winners Table
# =================================
class TriviaDrawWinner(Base):
    __tablename__ = "trivia_draw_winners"
    
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
class DrawConfig(Base):
    __tablename__ = "draw_config"
    
    id = Column(Integer, primary_key=True, index=True)
    is_custom = Column(Boolean, default=False)  # Whether using custom winner count
    custom_winner_count = Column(Integer, nullable=True)  # Custom number of winners
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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