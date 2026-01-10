import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from db import get_db

from .schemas import BindPasswordData, DevSignInRequest, ReferralCheck
from .service import (
    bind_password,
    check_email_available,
    check_username_available,
    dev_sign_in,
    get_countries,
    validate_referral_code,
)

router = APIRouter()


@router.get("/username-available")
def username_available(username: str, request: Request, db: Session = Depends(get_db)):
    return check_username_available(username, request, db)


@router.get("/email-available")
def email_available(email: str, request: Request, db: Session = Depends(get_db)):
    return check_email_available(email, request, db)


@router.post("/bind-password")
def bind_password_endpoint(
    request: Request, data: BindPasswordData, db: Session = Depends(get_db)
):
    return bind_password(request, data, db)


@router.post("/dev/sign-in")
def dev_sign_in_endpoint(
    request: Request,
    data: DevSignInRequest,
    x_dev_secret: str = Header(
        None,
        alias="X-Dev-Secret",
        description="Dev-only secret to authorize",
        example="TriviaPay",
    ),
):
    dev_secret = os.getenv("DEV_ADMIN_SECRET")
    if not dev_secret or x_dev_secret != dev_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return dev_sign_in(data.email.strip(), data.password)


@router.post("/validate-referral")
def validate_referral_code_endpoint(
    referral_data: ReferralCheck, db: Session = Depends(get_db)
):
    return validate_referral_code(referral_data.referral_code, db)


@router.get("/countries")
def get_countries_endpoint():
    return get_countries()
