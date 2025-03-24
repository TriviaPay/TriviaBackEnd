from sqlalchemy import (
    Column, Integer, String, Float, Boolean, ForeignKey, DateTime, BigInteger
)
from sqlalchemy.orm import relationship
from db import Base
from datetime import datetime
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

    subscriber_number = Column(String, nullable=True)
    username = Column(String, nullable=True)
    subscription_flag = Column(Boolean, default=False)
    sign_up_date = Column(DateTime, default=datetime.utcnow, nullable=False)
    refresh_token = Column(String, nullable=True)


    # Relationships
    winners = relationship("Winner", back_populates="user")
    entries = relationship("Entry", back_populates="user")
    payments = relationship("Payment", back_populates="user")
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

    account_id = Column(Integer, ForeignKey("users.account_id"), primary_key=True)
    number_of_entries = Column(Integer, nullable=False)
    ques_attempted = Column(Integer, nullable=False)
    correct_answers = Column(Integer, nullable=False)
    wrong_answers = Column(Integer, nullable=False)

    # Relationship
    user = relationship("User", back_populates="entries")


# =================================
#  Winners Table
# =================================
class Winner(Base):
    __tablename__ = "winners"

    account_id = Column(Integer, ForeignKey("users.account_id"), primary_key=True)
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

    account_id = Column(Integer, ForeignKey("users.account_id"), primary_key=True)
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
    account_id = Column(Integer, ForeignKey("users.account_id"), nullable=False)
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
    sender_account_id = Column(Integer, ForeignKey("users.account_id"), nullable=False)
    receiver_account_id = Column(Integer, ForeignKey("users.account_id"), nullable=False)
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
    account_id = Column(Integer, ForeignKey("users.account_id"), nullable=False)
    amount = Column(Float, nullable=False)
    withdrawal_method = Column(String, nullable=False)  # e.g. "bank", "paypal", ...
    withdrawal_status = Column(String, nullable=False)  # e.g. "requested", "completed", "failed"
    requested_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)

    # Relationship to user if desired
    user = relationship("User", backref="withdrawals")