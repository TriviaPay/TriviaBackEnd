import random
import string

from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import User

REFERRAL_CODE_LENGTH = 5
REFERRAL_CHARSET = string.ascii_uppercase + string.digits


def generate_referral_code() -> str:
    """Return a random 5-character alphanumeric referral code."""
    return ''.join(random.choices(REFERRAL_CHARSET, k=REFERRAL_CODE_LENGTH))


def get_unique_referral_code(db: Session, max_attempts: int = 10) -> str:
    """
    Return a referral code that is not yet in use.
    Raises HTTPException if no unique code can be generated after several attempts.
    """
    for _ in range(max_attempts):
        code = generate_referral_code()
        if not db.query(User).filter(User.referral_code == code).first():
            return code

    raise HTTPException(
        status_code=500,
        detail="Unable to generate a unique referral code. Please try again shortly.",
    )
