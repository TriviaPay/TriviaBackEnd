"""Domain service layer."""

import json
import logging
import os
import random
import re
import threading
import time
import uuid
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import date, datetime, timedelta
from typing import Any, Deque, Dict, Optional, Tuple

import redis  # type: ignore
from descope.descope_client import DescopeClient
from fastapi import HTTPException, Request, UploadFile, status
from passlib.context import CryptContext
from redis.exceptions import ConnectionError, RedisError, TimeoutError  # type: ignore
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import validate_descope_jwt
from config import (
    AWS_DEFAULT_PROFILE_PIC_BASE_URL,
    AWS_PROFILE_PIC_BUCKET,
    DESCOPE_JWT_LEEWAY,
    DESCOPE_JWT_LEEWAY_FALLBACK,
    DESCOPE_MANAGEMENT_KEY,
    DESCOPE_PROJECT_ID,
    REFERRAL_APP_LINK,
    STORE_PASSWORD_IN_DESCOPE,
    STORE_PASSWORD_IN_NEONDB,
)
from models import (
    Avatar,
    Frame,
    GemPackageConfig,
    SubscriptionPlan,
    TriviaModeConfig,
    User,
    UserAvatar,
    UserFrame,
    UserSubscription,
)
from utils.free_mode_rewards import (
    calculate_reward_distribution,
    cleanup_old_leaderboard,
    distribute_rewards_to_winners,
    get_eligible_participants_free_mode,
    rank_participants_by_completion,
)
from utils.question_upload_service import parse_csv_questions, save_questions_to_mode
from utils.referrals import get_unique_referral_code
from utils.storage import delete_file, presign_get, upload_file
from utils.subscription_service import get_modes_access_status
from utils.trivia_mode_service import (
    get_active_draw_date,
    get_mode_config,
    get_today_in_app_timezone,
)
from utils.user_level_service import count_total_correct_answers, get_level_progress

from . import repository as auth_repository

logger = logging.getLogger(__name__)

mgmt_client = DescopeClient(
    project_id=DESCOPE_PROJECT_ID,
    management_key=DESCOPE_MANAGEMENT_KEY,
    jwt_validation_leeway=DESCOPE_JWT_LEEWAY,
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

rate_limit_store: "OrderedDict[str, Deque[float]]" = OrderedDict()
rate_limit_lock = threading.Lock()
rate_limit_redis = None
rate_limit_redis_unavailable = False
rate_limit_redis_last_retry = 0.0
RATE_LIMIT_REDIS_RETRY_SECONDS = 60
RATE_LIMIT_WINDOW = 300  # 5 minutes
RATE_LIMIT_MAX_REQUESTS = 5  # 5 requests per window
RATE_LIMIT_MAX_KEYS = 10000

_SESSION_CACHE: Dict[str, Tuple[dict, float]] = {}
_SESSION_CACHE_TTL_SECONDS = int(os.getenv("DESCOPE_SESSION_CACHE_TTL_SECONDS", "30"))
_DESCOPE_VALIDATE_TIMEOUT_SECONDS = float(
    os.getenv("DESCOPE_VALIDATE_TIMEOUT_SECONDS", "5")
)
_DESCOPE_VALIDATE_MAX_WORKERS = int(os.getenv("DESCOPE_VALIDATE_MAX_WORKERS", "4"))
_DESCOPE_EXECUTOR = ThreadPoolExecutor(max_workers=_DESCOPE_VALIDATE_MAX_WORKERS)

DateType = date


def check_rate_limit(identifier: str) -> bool:
    """Check if the request is within rate limits."""
    now = time.time()
    global rate_limit_redis, rate_limit_redis_unavailable, rate_limit_redis_last_retry
    if (
        rate_limit_redis_unavailable
        and (now - rate_limit_redis_last_retry) < RATE_LIMIT_REDIS_RETRY_SECONDS
    ):
        rate_limit_redis = None
    elif rate_limit_redis_unavailable:
        rate_limit_redis_unavailable = False
        rate_limit_redis = None
    if rate_limit_redis is None and not rate_limit_redis_unavailable:
        try:
            from config import REDIS_URL

            rate_limit_redis = redis.Redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            rate_limit_redis.ping()
            rate_limit_redis_unavailable = False
        except Exception:
            rate_limit_redis_unavailable = True
            rate_limit_redis_last_retry = now
            rate_limit_redis = None

    if rate_limit_redis:
        try:
            key = f"rl:login:{identifier}"
            pipe = rate_limit_redis.pipeline()
            pipe.incr(key, 1)
            pipe.expire(key, RATE_LIMIT_WINDOW)
            count, _ = pipe.execute()
            return int(count) <= RATE_LIMIT_MAX_REQUESTS
        except (ConnectionError, TimeoutError, RedisError, OSError):
            rate_limit_redis_unavailable = True
            rate_limit_redis_last_retry = now
            rate_limit_redis = None

    with rate_limit_lock:
        bucket = rate_limit_store.get(identifier)
        if bucket is None:
            bucket = deque()
            rate_limit_store[identifier] = bucket
        else:
            rate_limit_store.move_to_end(identifier)

        while bucket and now - bucket[0] >= RATE_LIMIT_WINDOW:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            return False

        bucket.append(now)
        if len(rate_limit_store) > RATE_LIMIT_MAX_KEYS:
            rate_limit_store.popitem(last=False)

    return True


def get_default_profile_pic_url(username: str) -> Optional[str]:
    if not AWS_DEFAULT_PROFILE_PIC_BASE_URL:
        logging.warning(
            "AWS_DEFAULT_PROFILE_PIC_BASE_URL not configured, skipping default profile pic"
        )
        return None
    if not username:
        return None
    first_letter = username[0].lower()
    if not first_letter.isalpha():
        first_letter = "a"
    base_url = AWS_DEFAULT_PROFILE_PIC_BASE_URL.rstrip("/")
    return f"{base_url}/{first_letter}.png"


def _validate_password_strength(password: str):
    if len(password) < 8:
        raise HTTPException(
            status_code=400, detail="Password must be at least 8 characters long"
        )
    if not re.search(r"[A-Za-z]", password):
        raise HTTPException(
            status_code=400, detail="Password must contain at least one letter"
        )
    if not re.search(r"\d", password):
        raise HTTPException(
            status_code=400, detail="Password must contain at least one number"
        )


def _validate_username(username: str):
    if len(username) < 3 or len(username) > 30:
        raise HTTPException(
            status_code=400, detail="Username must be between 3 and 30 characters"
        )
    if not re.match(r"^[A-Za-z0-9_.-]+$", username):
        raise HTTPException(
            status_code=400,
            detail="Username may contain letters, numbers, and . _ - only",
        )


def _validate_country(country: str):
    if not country or len(country.strip()) < 2:
        raise HTTPException(status_code=400, detail="Country is required")
    if len(country) > 64:
        raise HTTPException(status_code=400, detail="Country is too long")


def _validate_date_of_birth(dob: DateType):
    today = datetime.utcnow().date()
    if dob >= today:
        raise HTTPException(status_code=400, detail="Date of birth must be in the past")
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    if age < 13:
        raise HTTPException(status_code=400, detail="You must be at least 13 years old")


def check_username_available(username: str, request: Request, db: Session):
    ip = request.client.host if request.client else "unknown"
    username_norm = username.strip()
    rl_key = f"ua:{ip}:{username_norm.lower()}"
    if not check_rate_limit(rl_key):
        raise HTTPException(
            status_code=429, detail="Too many requests. Please try again later."
        )
    exists = auth_repository.get_user_by_username_ci(db, username_norm)
    return {"available": exists is None}


def check_email_available(email: str, request: Request, db: Session):
    ip = request.client.host if request.client else "unknown"
    email_norm = email.strip().lower()
    rl_key = f"ea:{ip}:{email_norm}"
    if not check_rate_limit(rl_key):
        raise HTTPException(
            status_code=429, detail="Too many requests. Please try again later."
        )
    exists = auth_repository.get_user_by_email_ci(db, email_norm)
    return {"available": exists is None}


def bind_password(request: Request, data, db: Session):
    email = data.email.strip().lower()
    username = data.username.strip()
    country = data.country.strip()

    logging.info(
        f"[BIND_PASSWORD] 📝 Bind password request received - "
        f"LoginId: '{email}', "
        f"Username: '{username}', "
        f"Country: '{country}', "
        f"ReferralCode: '{data.referral_code if data.referral_code else 'None'}', "
        f"PasswordLength: {len(data.password)}, "
        f"Timestamp: '{datetime.utcnow().isoformat()}'"
    )

    content_type = request.headers.get("Content-Type", "")
    if "application/json" not in content_type:
        raise HTTPException(
            status_code=415, detail="Unsupported Media Type. Use application/json"
        )

    _validate_password_strength(data.password)
    _validate_username(username)
    _validate_country(country)
    _validate_date_of_birth(data.date_of_birth)

    ip = request.client.host if request.client else "unknown"
    rate_identifier = f"{ip}:{email}"
    if not check_rate_limit(rate_identifier):
        raise HTTPException(
            status_code=429, detail="Too many requests. Please try again later."
        )

    auth_header = request.headers.get("Authorization") or request.headers.get(
        "authorization"
    )
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")

    token = auth_header.split(" ", 1)[1].strip()
    user_info = validate_descope_jwt(token)

    user_id = user_info.get("userId") or user_info.get("sub")
    session_login_ids = user_info.get("loginIds") or []
    session_email = None
    if isinstance(session_login_ids, list) and len(session_login_ids) > 0:
        session_email = session_login_ids[0]
    if not session_email:
        session_email = user_info.get("email")

    if not user_id or not session_email:
        raise HTTPException(
            status_code=400, detail="Invalid user information from session"
        )

    if session_email.lower() != email:
        if not session_email.endswith("@descope.local"):
            raise HTTPException(
                status_code=403,
                detail="User mismatch: session email does not match payload email",
            )
        logging.info(
            f"Using provided email {email} instead of placeholder session email {session_email}"
        )

    try:
        try:
            user_details = mgmt_client.mgmt.user.load(user_id)
            logging.info(f"User exists in Descope, updating details: {user_id}")

            update_data = {
                "email": email,
                "display_name": username,
                "custom_attributes": {
                    "country": country,
                    "date_of_birth": str(data.date_of_birth),
                },
            }

            mgmt_client.mgmt.user.update(login_id=email, **update_data)

            if STORE_PASSWORD_IN_DESCOPE:
                try:
                    mgmt_client.mgmt.user.set_password(
                        login_id=email, password=data.password
                    )
                    logging.info(f"Password updated for existing user: {email}")
                except Exception:
                    logging.error(
                        f"[PASSWORD_BINDING] ❌ Failed to set password in Descope for EXISTING user - "
                        f"LoginId: '{email}', "
                        f"UserId: '{user_id}'",
                        exc_info=True,
                    )
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to set password in authentication system",
                    )
        except Exception as load_error:
            if "not found" in str(load_error).lower():
                logging.info(f"User not found in Descope, creating new user: {user_id}")
                try:
                    user_details = mgmt_client.mgmt.user.create(
                        login_id=email,
                        user_id=user_id,
                        email=email,
                        display_name=username,
                        custom_attributes={
                            "country": country,
                            "date_of_birth": str(data.date_of_birth),
                        },
                    )
                    logging.info(f"Created new user in Descope: {user_details}")
                except Exception as create_error:
                    logging.error(f"Failed to create Descope user: {create_error}")
                    raise HTTPException(
                        status_code=500,
                        detail="Failed to create user in authentication system. Please try again.",
                    )

                if STORE_PASSWORD_IN_DESCOPE:
                    try:
                        mgmt_client.mgmt.user.set_password(
                            login_id=email, password=data.password
                        )
                        logging.info(f"Password set for new user: {email}")
                        try:
                            user_details = mgmt_client.mgmt.user.load(user_id)
                            has_active_password = user_details.get(
                                "activePassword", False
                            )
                            logging.info(
                                f"[PASSWORD_BINDING] ✅ Password set and activated - "
                                f"LoginId: '{email}', "
                                f"ActivePasswordSet: {has_active_password}"
                            )
                            if not has_active_password:
                                logging.error(
                                    "[PASSWORD_BINDING] ⚠️ Password was set but not activated for new user! User may not be able to sign in."
                                )
                        except Exception:
                            logging.error(
                                f"[PASSWORD_BINDING] ❌ Failed to set password in Descope for NEW user - "
                                f"LoginId: '{email}', "
                                f"UserId: '{user_id}'",
                                exc_info=True,
                            )
                            raise HTTPException(
                                status_code=500,
                                detail="Failed to set password in authentication system",
                            )
                    except Exception as password_error:
                        logging.error(
                            f"[PASSWORD_BINDING] ❌ Failed to set password for NEW user - "
                            f"LoginId: '{email}', "
                            f"UserId: '{user_id}'",
                            exc_info=True,
                        )
                        raise HTTPException(
                            status_code=500,
                            detail="Failed to set password in authentication system",
                        ) from password_error

                    logging.info(f"Successfully created user in Descope: {user_id}")
            else:
                logging.error(f"Descope user operation failed: {load_error}")
                raise HTTPException(
                    status_code=500,
                    detail="Failed to sync user with authentication system. Please try again.",
                )

    except Exception as descope_error:
        logging.error(f"Descope management operation failed: {descope_error}")
        raise HTTPException(
            status_code=500,
            detail="Failed to sync user with authentication system. Please try again.",
        )

    existing_user = auth_repository.get_user_by_email_ci(db, email)
    if existing_user:
        existing_user.username = username
        existing_user.country = country
        existing_user.date_of_birth = data.date_of_birth
        existing_user.descope_user_id = user_id

        if not existing_user.profile_pic_url:
            profile_pic_url = get_default_profile_pic_url(username)
            if profile_pic_url:
                existing_user.profile_pic_url = profile_pic_url
                logging.info(
                    f"Set default profile pic for existing user: {profile_pic_url}"
                )

        if STORE_PASSWORD_IN_NEONDB:
            existing_user.password = pwd_context.hash(data.password)

        if data.referral_code and not existing_user.referred_by:
            try:
                referrer = auth_repository.get_user_by_referral_code_for_update(
                    db, data.referral_code
                )
                if not referrer:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid referral code '{data.referral_code}'. Please check and try again.",
                    )

                if referrer.account_id == existing_user.account_id:
                    raise HTTPException(
                        status_code=400,
                        detail="You cannot use your own referral code.",
                    )

                referrer.referral_count = (referrer.referral_count or 0) + 1
                existing_user.referred_by = data.referral_code
                logging.info(
                    f"[REFERRAL] Successfully applied referral code: {data.referral_code} from user {referrer.username} to {email}"
                )
            except HTTPException:
                db.rollback()
                raise
            except Exception as e:
                logging.error(f"Error processing referral code: {str(e)}")
                db.rollback()
                raise HTTPException(
                    status_code=500,
                    detail="Error processing referral code. Please try again.",
                )

        db.commit()
        logging.info(
            f"[LOCAL_DB] Updated existing user in local database - "
            f"Email: '{email}', "
            f"DescopeUserId: '{user_id}', "
            f"LocalPasswordStored: {STORE_PASSWORD_IN_NEONDB}, "
            f"ReferralCode: {data.referral_code if data.referral_code else 'None'}"
        )
    else:
        existing_username = auth_repository.get_user_by_username_ci(db, username)
        if existing_username:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "username_taken",
                    "message": f"Username '{username}' is already taken",
                },
            )

        referred_by_code = None
        if data.referral_code:
            try:
                referrer = auth_repository.get_user_by_referral_code_for_update(
                    db, data.referral_code
                )
                if not referrer:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid referral code '{data.referral_code}'. Please check and try again.",
                    )

                referrer.referral_count = (referrer.referral_count or 0) + 1
                referred_by_code = data.referral_code
                logging.info(
                    f"[REFERRAL] New user will be referred by: {data.referral_code} from user {referrer.username}"
                )
            except HTTPException:
                db.rollback()
                raise
            except Exception as e:
                logging.error(f"Error processing referral code: {str(e)}")
                db.rollback()
                raise HTTPException(
                    status_code=500,
                    detail="Error processing referral code. Please try again.",
                )

        profile_pic_url = get_default_profile_pic_url(username)
        if profile_pic_url:
            logging.info(
                f"Generated default profile pic URL for new user: {profile_pic_url}"
            )

        new_user = User(
            descope_user_id=user_id,
            email=email,
            username=username,
            country=country,
            date_of_birth=data.date_of_birth,
            profile_pic_url=profile_pic_url,
            notification_on=True,
            gems=0,
            referral_count=0,
            referral_code=get_unique_referral_code(db),
            is_admin=False,
            username_updated=False,
            subscription_flag=False,
            sign_up_date=datetime.utcnow(),
            wallet_balance=0.0,
            total_spent=0.0,
            referred_by=referred_by_code,
            password=(
                pwd_context.hash(data.password) if STORE_PASSWORD_IN_NEONDB else None
            ),
        )
        db.add(new_user)
        db.commit()
        logging.info(
            f"[LOCAL_DB] Created new user in local database - "
            f"Email: '{email}', "
            f"DescopeUserId: '{user_id}', "
            f"LocalPasswordStored: {STORE_PASSWORD_IN_NEONDB}, "
            f"ReferralCode: {data.referral_code if data.referral_code else 'None'}"
        )

    logging.info(
        f"[BIND_PASSWORD] ✅ Successfully completed password binding - "
        f"LoginId: '{email}', "
        f"UserId: '{user_id}', "
        f"Username: '{username}', "
        f"DescopePasswordSet: {STORE_PASSWORD_IN_DESCOPE}, "
        f"LocalPasswordStored: {STORE_PASSWORD_IN_NEONDB}, "
        f"Timestamp: '{datetime.utcnow().isoformat()}'"
    )

    return {"success": True, "message": "Password and profile bound successfully"}


def dev_sign_in(email: str, password: str):
    if os.getenv("ENVIRONMENT", "development") != "development":
        raise HTTPException(status_code=403, detail="Not available in this environment")

    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    if not password:
        raise HTTPException(status_code=400, detail="Password is required")

    try:
        project_id = os.getenv("DESCOPE_PROJECT_ID", DESCOPE_PROJECT_ID)
        if not project_id:
            raise HTTPException(
                status_code=500, detail="Descope project ID not configured"
            )

        client = DescopeClient(
            project_id=project_id, jwt_validation_leeway=DESCOPE_JWT_LEEWAY
        )

        try:
            response = client.password.sign_in(email, password)
        except AttributeError:
            try:
                response = client.auth.sign_in(email, password)
            except AttributeError:
                try:
                    response = client.password.sign_in(
                        login_id=email, password=password
                    )
                except Exception as e:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Descope SDK method not found. Available methods: {dir(client)}. Error: {str(e)}",
                    )

        session_jwt = None
        logging.debug(f"Sign-in response type: {type(response)}")
        logging.debug(f"Sign-in response: {response}")

        if hasattr(response, "session_jwt"):
            session_jwt = response.session_jwt
        elif hasattr(response, "sessionJwt"):
            session_jwt = response.sessionJwt
        elif isinstance(response, dict):
            if "sessionToken" in response and isinstance(
                response["sessionToken"], dict
            ):
                session_jwt = response["sessionToken"].get("jwt")

            if not session_jwt:
                session_jwt = (
                    response.get("sessionJwt")
                    or response.get("session_jwt")
                    or response.get("jwt")
                    or response.get("token")
                    or response.get("session_token")
                )
        elif hasattr(response, "__dict__"):
            resp_dict = response.__dict__
            session_jwt = (
                resp_dict.get("sessionJwt")
                or resp_dict.get("session_jwt")
                or resp_dict.get("jwt")
                or resp_dict.get("token")
                or resp_dict.get("session_token")
            )

        if not session_jwt:
            resp_str = str(response)
            if len(resp_str) > 50 and resp_str.startswith("eyJ"):
                session_jwt = resp_str

        if not session_jwt:
            logging.error(
                f"No session JWT found in response. Response type: {type(response)}, Response: {response}"
            )
            raise HTTPException(
                status_code=502,
                detail=f"No session JWT found in response. Response type: {type(response)}",
            )

        logging.info(f"[DEV_SIGN_IN] ✅ Successfully signed in user: {email}")

        return {"access_token": session_jwt}

    except ImportError:
        raise HTTPException(status_code=500, detail="Descope Python SDK not installed")
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        logging.error(f"[DEV_SIGN_IN] ❌ Failed to sign in user {email}: {error_msg}")

        if (
            "invalid" in error_msg.lower()
            or "incorrect" in error_msg.lower()
            or "wrong" in error_msg.lower()
        ):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        if "not found" in error_msg.lower() or "does not exist" in error_msg.lower():
            raise HTTPException(status_code=404, detail="User not found")
        if "locked" in error_msg.lower() or "blocked" in error_msg.lower():
            raise HTTPException(status_code=403, detail="Account is locked or blocked")
        raise HTTPException(status_code=502, detail=f"Failed to sign in: {error_msg}")


def validate_referral_code(referral_code: str, db: Session):
    try:
        referrer = auth_repository.get_user_by_referral_code(db, referral_code)
        if not referrer:
            return {
                "status": "error",
                "message": f"Invalid referral code '{referral_code}'. Please check and try again.",
                "code": "INVALID_REFERRAL_CODE",
                "valid": False,
            }

        return {
            "status": "success",
            "message": "Referral code is valid.",
            "referrer_username": (
                referrer.username if referrer.username else "Anonymous User"
            ),
            "valid": True,
        }

    except Exception as e:
        logging.error(f"Error validating referral code: {str(e)}")
        return {
            "status": "error",
            "message": f"An error occurred while validating the referral code: {str(e)}",
            "code": "VALIDATE_REFERRAL_ERROR",
            "valid": False,
        }


def get_countries():
    try:
        countries = [
            "United States",
            "Canada",
            "United Kingdom",
            "Australia",
            "India",
            "Germany",
            "France",
            "Japan",
            "Brazil",
            "Mexico",
            "China",
            "Spain",
            "Italy",
            "Russia",
            "South Korea",
            "Singapore",
            "New Zealand",
            "South Africa",
            "Nigeria",
            "Kenya",
            "Egypt",
            "Saudi Arabia",
            "United Arab Emirates",
            "Pakistan",
            "Bangladesh",
            "Malaysia",
            "Indonesia",
            "Philippines",
            "Vietnam",
            "Thailand",
        ]

        return {
            "status": "success",
            "countries": sorted(countries),
            "country_codes": [],
        }
    except Exception as e:
        logging.error(f"Error fetching countries: {str(e)}")
        return {
            "status": "error",
            "message": f"An unexpected error occurred: {str(e)}",
            "code": "UNEXPECTED_ERROR",
        }


def _get_cached_session(token: str) -> Optional[dict]:
    cached = _SESSION_CACHE.get(token)
    if not cached:
        return None
    session, expires_at = cached
    if expires_at > time.time():
        return session
    _SESSION_CACHE.pop(token, None)
    return None


def _set_cached_session(token: str, session: dict) -> None:
    if _SESSION_CACHE_TTL_SECONDS <= 0:
        return
    _SESSION_CACHE[token] = (session, time.time() + _SESSION_CACHE_TTL_SECONDS)
    if len(_SESSION_CACHE) > 2000:
        _SESSION_CACHE.clear()


def _validate_session_with_timeout(client: DescopeClient, token: str) -> dict:
    future = _DESCOPE_EXECUTOR.submit(client.validate_session, token)
    try:
        return future.result(timeout=_DESCOPE_VALIDATE_TIMEOUT_SECONDS)
    except FutureTimeoutError as exc:
        future.cancel()
        raise HTTPException(
            status_code=504, detail="Session validation timed out"
        ) from exc


def refresh_session(request: Request, db: Session):
    auth_header = request.headers.get("authorization", "").strip()
    if not auth_header:
        raise HTTPException(status_code=401, detail="No authorization header found")

    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    else:
        token = auth_header

    token = token.strip()
    token = "".join(token.split())

    logger.info("Attempting to refresh Descope session")

    try:
        session = _get_cached_session(token)
        if session is None:
            session = _validate_session_with_timeout(mgmt_client, token)
            _set_cached_session(token, session)

        user_id = session.get("userId") or session.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=400, detail="Invalid session: no user ID found"
            )

        user_info = {
            "userId": user_id,
            "sub": session.get("sub"),
            "loginIds": session.get("loginIds", []),
            "email": (
                session.get("loginIds", [None])[0] if session.get("loginIds") else None
            ),
            "name": session.get("name"),
            "displayName": session.get("displayName"),
        }

        user = auth_repository.get_user_by_descope_id(db, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found; please login")

        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "user_info": user_info,
            "message": "Session validated successfully",
        }

    except Exception as e:
        logger.error(f"Session refresh failed: {str(e)}")

        if "time glitch" in str(e).lower() or "jwt_validation_leeway" in str(e).lower():
            try:
                high_leeway_client = DescopeClient(
                    project_id=DESCOPE_PROJECT_ID,
                    management_key=DESCOPE_MANAGEMENT_KEY,
                    jwt_validation_leeway=DESCOPE_JWT_LEEWAY_FALLBACK,
                )
                session = _get_cached_session(token)
                if session is None:
                    session = _validate_session_with_timeout(high_leeway_client, token)
                    _set_cached_session(token, session)

                user_id = session.get("userId") or session.get("sub")
                user_info = {
                    "userId": user_id,
                    "sub": session.get("sub"),
                    "loginIds": session.get("loginIds", []),
                    "email": (
                        session.get("loginIds", [None])[0]
                        if session.get("loginIds")
                        else None
                    ),
                    "name": session.get("name"),
                    "displayName": session.get("displayName"),
                }

                return {
                    "access_token": token,
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "user_info": user_info,
                    "message": "Session validated with extended leeway",
                }
            except Exception as e2:
                logger.error(f"High leeway validation also failed: {str(e2)}")

        raise HTTPException(
            status_code=401, detail="Session refresh failed: Invalid or expired token"
        )


_gender_column_checked = False


def _ensure_gender_column(db: Session) -> None:
    global _gender_column_checked
    if _gender_column_checked:
        return
    connection = None
    try:
        connection = db.bind.connect()
        exists = connection.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name='users' AND column_name='gender'"
            )
        ).scalar()
        if exists:
            _gender_column_checked = True
            return

        allow_ddl = os.getenv("PROFILE_ALLOW_GENDER_DDL", "false").lower() in {
            "1",
            "true",
            "yes",
        }
        if allow_ddl:
            with connection.begin():
                connection.execute(
                    text("ALTER TABLE users ADD COLUMN IF NOT EXISTS gender VARCHAR")
                )
            logging.info("Added missing gender column via runtime DDL")
        else:
            logging.error(
                "Gender column missing; set PROFILE_ALLOW_GENDER_DDL=true or run migrations"
            )
        _gender_column_checked = True
    except Exception as exc:
        logging.error(f"Failed to ensure gender column exists: {exc}", exc_info=True)
        _gender_column_checked = True
    finally:
        if connection is not None:
            connection.close()


def get_badge_info(user: User, db: Session):
    if not user.badge_id:
        return None
    mode_config = auth_repository.get_mode_config_by_id(db, user.badge_id)
    if not mode_config or not mode_config.badge_image_url:
        return None
    return {
        "id": mode_config.mode_id,
        "name": mode_config.mode_name,
        "image_url": mode_config.badge_image_url,
    }


def get_recent_draw_earnings(user: User, db: Session) -> float:
    try:
        active_date = get_active_draw_date()
        today = get_today_in_app_timezone()
        draw_date = active_date if active_date == today else active_date
        total = auth_repository.get_recent_draw_earnings_sum(
            db, user.account_id, draw_date
        )
        return round(float(total or 0.0), 2)
    except Exception as exc:
        logging.error(
            f"Error getting recent draw earnings for user {user.account_id}: {str(exc)}"
        )
        return 0.0


def get_subscription_badges(user: User, db: Session):
    subscription_badges = []
    active_subscriptions = auth_repository.get_active_subscription_prices(
        db, user.account_id
    )

    has_bronze = any(
        unit_amount_minor == 500 or price_usd == 5.0
        for unit_amount_minor, price_usd in active_subscriptions
    )
    has_silver = any(
        unit_amount_minor == 1000 or price_usd == 10.0
        for unit_amount_minor, price_usd in active_subscriptions
    )

    badge_map = {}
    if has_bronze or has_silver:
        badge_candidates = [
            "bronze",
            "bronze_badge",
            "brone_badge",
            "brone",
            "silver",
            "silver_badge",
        ]
        badges = auth_repository.get_badges_by_mode_ids(db, badge_candidates)
        badge_map = {badge.mode_id: badge for badge in badges}

    bronze_badge = None
    if has_bronze:
        for mode_id in ["bronze", "bronze_badge", "brone_badge", "brone"]:
            bronze_badge = badge_map.get(mode_id)
            if bronze_badge:
                break
        if not bronze_badge:
            bronze_badge = auth_repository.get_badge_by_mode_name_like(db, "%bronze%")

    if bronze_badge and bronze_badge.badge_image_url:
        subscription_badges.append(
            {
                "id": bronze_badge.mode_id,
                "name": bronze_badge.mode_name,
                "image_url": bronze_badge.badge_image_url,
                "subscription_type": "bronze",
                "price": 5.0,
            }
        )

    silver_badge = None
    if has_silver:
        for mode_id in ["silver", "silver_badge"]:
            silver_badge = badge_map.get(mode_id)
            if silver_badge:
                break
        if not silver_badge:
            silver_badge = auth_repository.get_badge_by_mode_name_like(db, "%silver%")

    if silver_badge and silver_badge.badge_image_url:
        subscription_badges.append(
            {
                "id": silver_badge.mode_id,
                "name": silver_badge.mode_name,
                "image_url": silver_badge.badge_image_url,
                "subscription_type": "silver",
                "price": 10.0,
            }
        )

    return subscription_badges


def get_user_gems(db: Session, current_user: User):
    try:
        user = auth_repository.get_user_by_account_id(db, current_user.account_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        badge_info = get_badge_info(user, db)
        subscription_badges = get_subscription_badges(user, db)
        recent_draw_earnings = get_recent_draw_earnings(user, db)

        return {
            "status": "success",
            "username": user.username,
            "gems": user.gems,
            "badge": badge_info,
            "subscription_badges": subscription_badges,
            "recent_draw_earnings": recent_draw_earnings,
        }
    except HTTPException:
        raise
    except Exception:
        logging.error("Error retrieving gems", exc_info=True)
        return {"status": "error", "message": "An error occurred while retrieving gems"}


async def update_extended_profile(
    request: Request, profile, db: Session, current_user: User
):
    try:
        user = auth_repository.get_user_by_account_id(db, current_user.account_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if profile.first_name is not None:
            user.first_name = profile.first_name
        if profile.last_name is not None:
            user.last_name = profile.last_name
        if profile.mobile is not None:
            user.mobile = profile.mobile
        if profile.country_code is not None:
            user.country_code = profile.country_code
        if profile.gender is not None:
            _ensure_gender_column(db)
            user.gender = profile.gender

        if profile.street_1 is not None:
            user.street_1 = profile.street_1
        if profile.street_2 is not None:
            user.street_2 = profile.street_2
        if profile.suite_or_apt_number is not None:
            user.suite_or_apt_number = profile.suite_or_apt_number
        if profile.city is not None:
            user.city = profile.city
        if profile.state is not None:
            user.state = profile.state
        if profile.zip is not None:
            user.zip = profile.zip
        if profile.country is not None:
            user.country = profile.country

        try:
            db.commit()
            logging.info(
                f"Extended profile successfully updated for user: {user.username}"
            )

            badge_info = get_badge_info(user, db)

            wallet_balance_minor = (
                user.wallet_balance_minor
                if hasattr(user, "wallet_balance_minor")
                and user.wallet_balance_minor is not None
                else int((user.wallet_balance or 0) * 100)
            )
            wallet_balance_usd = (
                wallet_balance_minor / 100.0 if wallet_balance_minor else 0.0
            )

            total_correct = count_total_correct_answers(user, db)
            level_info = get_level_progress(user, db, total_correct=total_correct)
            recent_draw_earnings = get_recent_draw_earnings(user, db)

            return {
                "status": "success",
                "message": "Profile updated successfully",
                "data": {
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "mobile": user.mobile,
                    "country_code": user.country_code,
                    "gender": getattr(user, "gender", None),
                    "address": {
                        "street_1": user.street_1,
                        "street_2": user.street_2,
                        "suite_or_apt_number": user.suite_or_apt_number,
                        "city": user.city,
                        "state": user.state,
                        "zip": user.zip,
                        "country": user.country,
                    },
                    "username_updated": user.username_updated,
                    "badge": badge_info,
                    "total_gems": user.gems or 0,
                    "total_trivia_coins": wallet_balance_usd,
                    "level": user.level if user.level else 1,
                },
            }
        except IntegrityError as exc:
            db.rollback()
            error_str = str(exc).lower()
            logging.error(f"Database integrity error: {error_str}")
            return {
                "status": "error",
                "message": "Database error while updating profile. Please try again.",
                "code": "DB_INTEGRITY_ERROR",
            }
    except HTTPException:
        raise
    except Exception:
        logging.error("Error updating extended profile", exc_info=True)
        return {
            "status": "error",
            "message": "An unexpected error occurred",
            "code": "UNEXPECTED_ERROR",
        }


async def get_complete_profile(db: Session, current_user: User):
    try:
        user = auth_repository.get_user_by_account_id(db, current_user.account_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        def _safe_iso_format(value):
            if not value:
                return None
            if isinstance(value, str):
                return value
            if hasattr(value, "isoformat"):
                return value.isoformat()
            return str(value)

        dob_formatted = _safe_iso_format(user.date_of_birth)
        signup_date_formatted = _safe_iso_format(user.sign_up_date)

        badge_info = get_badge_info(user, db)
        subscription_badges = get_subscription_badges(user, db)

        wallet_balance_minor = (
            user.wallet_balance_minor
            if hasattr(user, "wallet_balance_minor")
            and user.wallet_balance_minor is not None
            else int((user.wallet_balance or 0) * 100)
        )
        wallet_balance_usd = (
            wallet_balance_minor / 100.0 if wallet_balance_minor else 0.0
        )

        total_correct = count_total_correct_answers(user, db)
        level_info = get_level_progress(user, db, total_correct=total_correct)
        recent_draw_earnings = get_recent_draw_earnings(user, db)

        return {
            "status": "success",
            "data": {
                "account_id": user.account_id,
                "email": user.email,
                "mobile": user.mobile,
                "country_code": user.country_code,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "middle_name": user.middle_name,
                "username": user.username,
                "gender": getattr(user, "gender", None),
                "date_of_birth": dob_formatted,
                "sign_up_date": signup_date_formatted,
                "address": {
                    "street_1": user.street_1,
                    "street_2": user.street_2,
                    "suite_or_apt_number": user.suite_or_apt_number,
                    "city": user.city,
                    "state": user.state,
                    "zip": user.zip,
                    "country": user.country,
                },
                "profile_pic_url": user.profile_pic_url,
                "username_updated": user.username_updated,
                "referral_code": user.referral_code,
                "is_referred": bool(user.referred_by),
                "badge": badge_info,
                "subscription_badges": subscription_badges,
                "total_gems": user.gems or 0,
                "total_trivia_coins": wallet_balance_usd,
                "level": level_info["level"],
                "level_progress": level_info["progress"],
                "recent_draw_earnings": recent_draw_earnings,
            },
        }
    except HTTPException:
        raise
    except Exception:
        logging.error("Error fetching complete profile", exc_info=True)
        return {
            "status": "error",
            "message": "An unexpected error occurred",
            "code": "UNEXPECTED_ERROR",
        }


def change_username(new_username: str, user: User, db: Session):
    try:
        if user.username_updated:
            raise HTTPException(
                status_code=403,
                detail="Username change not allowed. Please purchase a username change.",
            )
        mgmt_client.mgmt.user.update(
            user_id=user.descope_user_id,
            update_data={"displayName": new_username, "name": new_username},
        )
        user.username = new_username
        user.username_updated = True
        db.commit()
        return {"success": True, "username": new_username}
    except Exception as exc:
        logging.error(f"/change-username error: {exc}")
        raise HTTPException(status_code=400, detail="Something went wrong")


async def get_profile_summary(db: Session, current_user: User):
    try:
        user = auth_repository.get_user_by_account_id(db, current_user.account_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        avatar_obj = None
        frame_obj = None

        if user.selected_avatar_id:
            avatar_obj = auth_repository.get_avatar_by_id(db, user.selected_avatar_id)
        if user.selected_frame_id:
            frame_obj = auth_repository.get_frame_by_id(db, user.selected_frame_id)

        avatar_payload = None
        if avatar_obj:
            signed = None
            bucket = getattr(avatar_obj, "bucket", None)
            object_key = getattr(avatar_obj, "object_key", None)
            if bucket and object_key:
                try:
                    signed = presign_get(bucket, object_key, expires=900)
                    if not signed:
                        logging.warning(
                            f"presign_get returned None for avatar {avatar_obj.id} with bucket={bucket}, key={object_key}"
                        )
                except Exception as exc:
                    logging.error(
                        f"Failed to presign avatar {avatar_obj.id}: {exc}",
                        exc_info=True,
                    )
            else:
                logging.debug(
                    f"Avatar {avatar_obj.id} missing bucket/object_key: bucket={bucket}, object_key={object_key}"
                )
            avatar_payload = {
                "id": avatar_obj.id,
                "name": avatar_obj.name,
                "url": signed,
                "mime_type": getattr(avatar_obj, "mime_type", None),
            }

        frame_payload = None
        if frame_obj:
            signed = None
            bucket = getattr(frame_obj, "bucket", None)
            object_key = getattr(frame_obj, "object_key", None)
            if bucket and object_key:
                try:
                    signed = presign_get(bucket, object_key, expires=900)
                    if not signed:
                        logging.warning(
                            f"presign_get returned None for frame {frame_obj.id} with bucket={bucket}, key={object_key}"
                        )
                except Exception as exc:
                    logging.error(
                        f"Failed to presign frame {frame_obj.id}: {exc}", exc_info=True
                    )
            else:
                logging.debug(
                    f"Frame {frame_obj.id} missing bucket/object_key: bucket={bucket}, object_key={object_key}"
                )
            frame_payload = {
                "id": frame_obj.id,
                "name": frame_obj.name,
                "url": signed,
                "mime_type": getattr(frame_obj, "mime_type", None),
            }

        badge_info = get_badge_info(user, db)
        subscription_badges = get_subscription_badges(user, db)

        wallet_balance_minor = (
            user.wallet_balance_minor
            if hasattr(user, "wallet_balance_minor")
            and user.wallet_balance_minor is not None
            else int((user.wallet_balance or 0) * 100)
        )
        wallet_balance_usd = (
            wallet_balance_minor / 100.0 if wallet_balance_minor else 0.0
        )

        recent_draw_earnings = get_recent_draw_earnings(user, db)

        profile_pic_type = None
        if user.profile_pic_url:
            profile_pic_type = "custom"
        elif user.selected_avatar_id:
            profile_pic_type = "avatar"
        else:
            profile_pic_type = "default"

        def _safe_iso(value):
            if not value:
                return None
            if hasattr(value, "isoformat"):
                return value.isoformat()
            if isinstance(value, str):
                return value
            return str(value)

        return {
            "status": "success",
            "data": {
                "username": user.username,
                "account_id": user.account_id,
                "email": user.email,
                "date_of_birth": _safe_iso(user.date_of_birth),
                "gender": getattr(user, "gender", None),
                "address1": user.street_1,
                "address2": user.street_2,
                "apt_number": user.suite_or_apt_number,
                "city": user.city,
                "state": user.state,
                "country": user.country,
                "zip": user.zip,
                "profile_pic_url": user.profile_pic_url,
                "profile_pic_type": profile_pic_type,
                "avatar": avatar_payload,
                "frame": frame_payload,
                "badge": badge_info,
                "subscription_badges": subscription_badges,
                "total_gems": user.gems or 0,
                "total_trivia_coins": wallet_balance_usd,
                "level": user.level if user.level else 1,
                "level_progress": get_level_progress(user, db)["progress"],
                "recent_draw_earnings": recent_draw_earnings,
            },
        }
    except HTTPException:
        raise
    except Exception:
        logging.error("Error fetching profile summary", exc_info=True)
        return {
            "status": "error",
            "message": "An unexpected error occurred",
            "code": "UNEXPECTED_ERROR",
        }


async def send_referral(db: Session, current_user: User):
    try:
        user = auth_repository.get_user_by_account_id(db, current_user.account_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if not user.referral_code:
            user.referral_code = get_unique_referral_code(db)
            db.commit()
            db.refresh(user)

        share_text = (
            f"Send code {user.referral_code} to friends so they can join TriviaPay."
        )
        logging.info(
            f"[REFERRAL] Sharing code {user.referral_code} for user {user.account_id} ({user.email})"
        )

        return {
            "status": "success",
            "message": "Referral code ready to share",
            "data": {
                "referral_code": user.referral_code,
                "share_text": share_text,
                "app_link": REFERRAL_APP_LINK,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        logging.error(f"Error preparing referral invite: {str(exc)}")
        raise HTTPException(
            status_code=500,
            detail="Unable to prepare referral invite. Please try again later.",
        )


async def upload_profile_picture(file: UploadFile, db: Session, current_user: User):
    try:
        if not AWS_PROFILE_PIC_BUCKET:
            raise HTTPException(
                status_code=500,
                detail="Profile picture upload is not configured. Please contact support.",
            )

        allowed_types = [
            "image/png",
            "image/jpeg",
            "image/jpg",
            "image/gif",
            "image/webp",
        ]
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type. Allowed types: {', '.join(allowed_types)}",
            )

        file_content = await file.read()
        max_size = 5 * 1024 * 1024
        if len(file_content) > max_size:
            raise HTTPException(
                status_code=400, detail="File size exceeds maximum allowed size of 5MB"
            )

        extension_map = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
        }
        _ = extension_map.get(file.content_type, "jpg")

        if current_user.account_id:
            identifier = str(current_user.account_id)
        elif current_user.email:
            identifier = current_user.email.replace("@", "_at_").replace(".", "_")
        else:
            identifier = str(uuid.uuid4())

        s3_key = f"profile_pic/{identifier}.jpg"

        old_extensions = ["png", "jpeg", "gif", "webp"]
        for ext in old_extensions:
            old_key = f"profile_pic/{identifier}.{ext}"
            if old_key != s3_key:
                delete_file(bucket=AWS_PROFILE_PIC_BUCKET, key=old_key)

        upload_success = upload_file(
            bucket=AWS_PROFILE_PIC_BUCKET,
            key=s3_key,
            file_content=file_content,
            content_type=file.content_type,
        )

        if not upload_success:
            raise HTTPException(
                status_code=500,
                detail="Failed to upload profile picture. Please try again.",
            )

        profile_pic_url = presign_get(
            bucket=AWS_PROFILE_PIC_BUCKET,
            key=s3_key,
            expires=31536000,
        )
        if not profile_pic_url:
            bucket_region = os.getenv("AWS_REGION", "us-east-2")
            profile_pic_url = f"https://{AWS_PROFILE_PIC_BUCKET}.s3.{bucket_region}.amazonaws.com/{s3_key}"

        user = auth_repository.get_user_by_account_id(db, current_user.account_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        user.selected_avatar_id = None
        user.profile_pic_url = profile_pic_url
        db.commit()

        badge_info = get_badge_info(user, db)
        logging.info(
            f"Profile picture uploaded successfully for user {user.account_id}"
        )

        return {
            "status": "success",
            "message": "Profile picture uploaded successfully",
            "data": {
                "profile_pic_url": profile_pic_url,
                "profile_pic_type": "custom",
                "badge": badge_info,
            },
        }

    except HTTPException:
        raise
    except Exception:
        db.rollback()
        logging.error("Error uploading profile picture", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while uploading profile picture",
        )


def get_all_modes_status(user: User, db: Session):
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    access_map = get_modes_access_status(db, user, ["free_mode", "bronze", "silver"])
    free_mode_access = access_map.get("free_mode", {})
    bronze_mode_access = access_map.get("bronze", {})
    silver_mode_access = access_map.get("silver", {})

    return {
        "free_mode": {
            "has_access": free_mode_access["has_access"],
            "subscription_status": free_mode_access.get(
                "subscription_status", "not_required"
            ),
            "subscription_details": free_mode_access.get("subscription_details"),
            "mode_name": "Free Mode",
            "price": 0.0,
        },
        "bronze_mode": {
            "has_access": bronze_mode_access["has_access"],
            "subscription_status": bronze_mode_access.get(
                "subscription_status", "no_subscription"
            ),
            "subscription_details": bronze_mode_access.get("subscription_details"),
            "mode_name": "Bronze Mode",
            "price": 5.0,
        },
        "silver_mode": {
            "has_access": silver_mode_access["has_access"],
            "subscription_status": silver_mode_access.get(
                "subscription_status", "no_subscription"
            ),
            "subscription_details": silver_mode_access.get("subscription_details"),
            "mode_name": "Silver Mode",
            "price": 10.0,
        },
    }


def _select_random_rows(base_query, count: int, order_col):
    total = base_query.count()
    if total <= 0:
        return []
    if total <= count:
        return base_query.order_by(order_col).all()

    offsets = random.sample(range(total), count)
    results = []
    seen_ids = set()
    for offset in offsets:
        row = base_query.order_by(order_col).offset(offset).limit(1).first()
        if row and row.id not in seen_ids:
            results.append(row)
            seen_ids.add(row.id)
    while len(results) < count:
        offset = random.randrange(total)
        row = base_query.order_by(order_col).offset(offset).limit(1).first()
        if row and row.id not in seen_ids:
            results.append(row)
            seen_ids.add(row.id)
    return results


def list_trivia_modes(db: Session):
    modes = db.query(TriviaModeConfig).all()
    result = []
    for mode in modes:
        try:
            reward_dist = (
                json.loads(mode.reward_distribution) if mode.reward_distribution else {}
            )
            ad_config = json.loads(mode.ad_config) if mode.ad_config else None
            survey_config = (
                json.loads(mode.survey_config) if mode.survey_config else None
            )
            leaderboard_types = (
                json.loads(mode.leaderboard_types) if mode.leaderboard_types else []
            )
        except Exception:
            reward_dist = {}
            ad_config = None
            survey_config = None
            leaderboard_types = []
        result.append(
            {
                "mode_id": mode.mode_id,
                "mode_name": mode.mode_name,
                "questions_count": mode.questions_count,
                "reward_distribution": reward_dist,
                "amount": mode.amount,
                "leaderboard_types": leaderboard_types,
                "ad_config": ad_config,
                "survey_config": survey_config,
                "badge_image_url": getattr(mode, "badge_image_url", None),
                "badge_description": getattr(mode, "badge_description", None),
                "badge_level": getattr(mode, "badge_level", None),
                "created_at": mode.created_at,
                "updated_at": mode.updated_at,
            }
        )
    return result


def create_or_update_trivia_mode(
    db: Session,
    mode_id: str,
    mode_name: str,
    questions_count: int,
    reward_distribution: dict,
    amount: float,
    leaderboard_types: list,
    ad_config: Optional[dict],
    survey_config: Optional[dict],
):
    existing = (
        db.query(TriviaModeConfig).filter(TriviaModeConfig.mode_id == mode_id).first()
    )
    if existing:
        existing.mode_name = mode_name
        existing.questions_count = questions_count
        existing.reward_distribution = json.dumps(reward_distribution)
        existing.amount = amount
        existing.leaderboard_types = json.dumps(leaderboard_types)
        existing.ad_config = json.dumps(ad_config) if ad_config else None
        existing.survey_config = json.dumps(survey_config) if survey_config else None
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return {"success": True, "message": f"Mode {mode_id} updated", "mode": existing}

    new_mode = TriviaModeConfig(
        mode_id=mode_id,
        mode_name=mode_name,
        questions_count=questions_count,
        reward_distribution=json.dumps(reward_distribution),
        amount=amount,
        leaderboard_types=json.dumps(leaderboard_types),
        ad_config=json.dumps(ad_config) if ad_config else None,
        survey_config=json.dumps(survey_config) if survey_config else None,
    )
    db.add(new_mode)
    db.commit()
    db.refresh(new_mode)
    return {"success": True, "message": f"Mode {mode_id} created", "mode": new_mode}


async def upload_questions_csv(
    db: Session, mode_id: str, file_content: bytes, max_bytes: int
):
    mode_config = get_mode_config(db, mode_id)
    if not mode_config:
        raise HTTPException(status_code=404, detail=f"Mode '{mode_id}' not found")

    if len(file_content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"CSV file too large (max {max_bytes} bytes)",
        )

    try:
        questions = parse_csv_questions(file_content, mode_id)
        result = save_questions_to_mode(db, questions, mode_id)
        return {
            "success": True,
            "saved_count": result["saved_count"],
            "duplicate_count": result["duplicate_count"],
            "error_count": result["error_count"],
            "errors": result["errors"][:10],
        }
    except ValueError:
        logging.warning("Invalid CSV upload", exc_info=True)
        raise HTTPException(status_code=400, detail="Invalid CSV file")


async def trigger_free_mode_draw(db: Session, draw_date: Optional[str]):
    if draw_date:
        try:
            target_date = date.fromisoformat(draw_date)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid date format. Use YYYY-MM-DD"
            )
    else:
        from utils.trivia_mode_service import get_active_draw_date

        target_date = get_active_draw_date() - date.resolution

    from models import TriviaFreeModeWinners

    existing_draw = (
        db.query(TriviaFreeModeWinners)
        .filter(TriviaFreeModeWinners.draw_date == target_date)
        .first()
    )
    if existing_draw:
        return {
            "status": "already_performed",
            "draw_date": target_date.isoformat(),
            "message": f"Draw for {target_date} has already been performed",
        }

    mode_config = get_mode_config(db, "free_mode")
    if not mode_config:
        raise HTTPException(status_code=404, detail="Free mode config not found")

    participants = get_eligible_participants_free_mode(db, target_date)
    if not participants:
        return {
            "status": "no_participants",
            "draw_date": target_date.isoformat(),
            "message": f"No eligible participants for draw on {target_date}",
        }

    ranked_participants = rank_participants_by_completion(participants)
    reward_info = calculate_reward_distribution(mode_config, len(ranked_participants))
    winner_count = reward_info["winner_count"]
    gem_amounts = reward_info["gem_amounts"]

    if len(ranked_participants) <= winner_count:
        winners_list = ranked_participants
    else:
        winners_list = ranked_participants[:winner_count]

    winners = []
    for i, participant in enumerate(winners_list):
        winners.append(
            {
                "account_id": participant["account_id"],
                "username": participant["username"],
                "position": i + 1,
                "gems_awarded": gem_amounts[i] if i < len(gem_amounts) else 0,
                "completed_at": participant["third_question_completed_at"],
            }
        )

    distribution_result = distribute_rewards_to_winners(
        db, winners, mode_config, target_date
    )
    previous_draw_date = target_date - date.resolution
    cleanup_old_leaderboard(db, previous_draw_date)

    return {
        "status": "success",
        "draw_date": target_date.isoformat(),
        "total_participants": len(ranked_participants),
        "total_winners": len(winners),
        "total_gems_awarded": distribution_result["total_gems_awarded"],
        "winners": winners,
    }


async def allocate_free_mode_questions_manual(db: Session, target_date: Optional[str]):
    from models import TriviaQuestionsFreeMode, TriviaQuestionsFreeModeDaily
    from utils.trivia_mode_service import get_active_draw_date, get_date_range_for_query

    if target_date:
        try:
            target = date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid date format. Use YYYY-MM-DD"
            )
    else:
        target = get_active_draw_date()

    mode_config = get_mode_config(db, "free_mode")
    if not mode_config:
        raise HTTPException(status_code=404, detail="Free mode config not found")

    questions_count = mode_config.questions_count
    start_datetime, end_datetime = get_date_range_for_query(target)

    existing_questions = (
        db.query(TriviaQuestionsFreeModeDaily)
        .filter(
            TriviaQuestionsFreeModeDaily.date >= start_datetime,
            TriviaQuestionsFreeModeDaily.date <= end_datetime,
        )
        .count()
    )

    if existing_questions > 0:
        return {
            "status": "already_allocated",
            "target_date": target.isoformat(),
            "existing_count": existing_questions,
            "message": f"Questions already allocated for {target}",
        }

    unused_query = db.query(TriviaQuestionsFreeMode).filter(
        TriviaQuestionsFreeMode.is_used == False
    )
    unused_count = unused_query.count()

    if unused_count < questions_count:
        all_query = db.query(TriviaQuestionsFreeMode)
        all_count = all_query.count()
        if all_count < questions_count:
            raise HTTPException(
                status_code=400,
                detail=f"Not enough questions available. Need {questions_count}, have {all_count}",
            )
        available_questions = _select_random_rows(
            all_query, questions_count, TriviaQuestionsFreeMode.id
        )
    else:
        available_questions = _select_random_rows(
            unused_query, questions_count, TriviaQuestionsFreeMode.id
        )

    if not available_questions:
        raise HTTPException(
            status_code=400, detail="No questions available to allocate"
        )

    allocated_count = 0
    for i, question in enumerate(available_questions[:questions_count], 1):
        daily_question = TriviaQuestionsFreeModeDaily(
            date=start_datetime,
            question_id=question.id,
            question_order=i,
            is_used=False,
        )
        db.add(daily_question)
        question.is_used = True
        allocated_count += 1

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {
            "status": "already_allocated",
            "target_date": target.isoformat(),
            "existing_count": existing_questions,
            "message": f"Questions already allocated for {target}",
        }

    return {
        "status": "success",
        "target_date": target.isoformat(),
        "allocated_count": allocated_count,
        "questions_count": questions_count,
        "message": f"Successfully allocated {allocated_count} questions for {target}",
    }


async def trigger_bronze_mode_draw(db: Session, draw_date: Optional[str]):
    if draw_date:
        try:
            target_date = date.fromisoformat(draw_date)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid date format. Use YYYY-MM-DD"
            )
    else:
        from utils.trivia_mode_service import get_active_draw_date

        target_date = get_active_draw_date() - date.resolution

    from models import TriviaBronzeModeWinners

    existing_draw = (
        db.query(TriviaBronzeModeWinners)
        .filter(TriviaBronzeModeWinners.draw_date == target_date)
        .first()
    )

    if existing_draw:
        return {
            "status": "already_performed",
            "draw_date": target_date.isoformat(),
            "message": f"Draw for {target_date} has already been performed",
        }

    from utils.bronze_mode_service import (
        cleanup_old_leaderboard_bronze_mode,
        distribute_rewards_to_winners_bronze_mode,
    )
    from utils.mode_draw_service import execute_mode_draw

    result = execute_mode_draw(db, "bronze", target_date)

    if result["status"] == "no_participants":
        return {
            "status": "no_participants",
            "draw_date": target_date.isoformat(),
            "message": f"No eligible participants for draw on {target_date}",
        }

    if result["status"] != "success":
        raise HTTPException(
            status_code=400, detail=result.get("message", "Error executing draw")
        )

    mode_config = get_mode_config(db, "bronze")
    if not mode_config:
        raise HTTPException(status_code=404, detail="Bronze mode config not found")

    winners = result.get("winners", [])
    total_pool = result.get("total_pool", 0.0)
    distribution_result = distribute_rewards_to_winners_bronze_mode(
        db, winners, target_date, total_pool
    )

    previous_draw_date = target_date - date.resolution
    cleanup_old_leaderboard_bronze_mode(db, previous_draw_date)

    return {
        "status": "success",
        "draw_date": target_date.isoformat(),
        "total_participants": result.get("total_participants", 0),
        "total_winners": len(winners),
        "total_money_awarded": distribution_result.get("total_money_awarded", 0.0),
        "winners": winners,
    }


async def allocate_bronze_mode_questions_manual(
    db: Session, target_date: Optional[str]
):
    from models import TriviaQuestionsBronzeMode, TriviaQuestionsBronzeModeDaily
    from utils.trivia_mode_service import get_active_draw_date, get_date_range_for_query

    if target_date:
        try:
            target = date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid date format. Use YYYY-MM-DD"
            )
    else:
        target = get_active_draw_date()

    mode_config = get_mode_config(db, "bronze")
    if not mode_config:
        raise HTTPException(status_code=404, detail="Bronze mode config not found")

    start_datetime, end_datetime = get_date_range_for_query(target)
    existing_question = (
        db.query(TriviaQuestionsBronzeModeDaily)
        .filter(
            TriviaQuestionsBronzeModeDaily.date >= start_datetime,
            TriviaQuestionsBronzeModeDaily.date <= end_datetime,
        )
        .count()
    )

    if existing_question > 0:
        return {
            "status": "already_allocated",
            "target_date": target.isoformat(),
            "existing_count": existing_question,
            "message": f"Question already allocated for {target}",
        }

    unused_query = db.query(TriviaQuestionsBronzeMode).filter(
        TriviaQuestionsBronzeMode.is_used == False
    )
    unused_count = unused_query.count()

    if unused_count < 1:
        all_query = db.query(TriviaQuestionsBronzeMode)
        all_count = all_query.count()
        if all_count < 1:
            raise HTTPException(
                status_code=400, detail="No questions available for bronze mode"
            )
        selected_questions = _select_random_rows(
            all_query, 1, TriviaQuestionsBronzeMode.id
        )
    else:
        selected_questions = _select_random_rows(
            unused_query, 1, TriviaQuestionsBronzeMode.id
        )

    if not selected_questions:
        raise HTTPException(
            status_code=400, detail="No questions available for bronze mode"
        )
    selected_question = selected_questions[0]

    daily_question = TriviaQuestionsBronzeModeDaily(
        date=start_datetime,
        question_id=selected_question.id,
        question_order=1,
        is_used=False,
    )
    db.add(daily_question)
    selected_question.is_used = True

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {
            "status": "already_allocated",
            "target_date": target.isoformat(),
            "existing_count": existing_question,
            "message": f"Question already allocated for {target}",
        }

    return {
        "status": "success",
        "target_date": target.isoformat(),
        "allocated_count": 1,
        "question_id": selected_question.id,
        "message": f"Successfully allocated question for {target}",
    }


def check_subscription_status(
    db: Session,
    plan_id: Optional[int],
    price_usd: Optional[float],
    user_id: Optional[int],
    plan_skip: int,
    plan_limit: int,
    sub_skip: int,
    sub_limit: int,
):
    plan_query = db.query(SubscriptionPlan)

    if plan_id:
        plan_query = plan_query.filter(SubscriptionPlan.id == plan_id)
    elif price_usd:
        plan_query = plan_query.filter(
            (SubscriptionPlan.price_usd == price_usd)
            | (SubscriptionPlan.unit_amount_minor == int(price_usd * 100))
        )

    plans = (
        plan_query.order_by(SubscriptionPlan.id)
        .offset(plan_skip)
        .limit(plan_limit)
        .all()
    )

    result = {"plans_found": len(plans), "plans": [], "subscriptions": []}

    for plan in plans:
        result["plans"].append(
            {
                "id": plan.id,
                "name": plan.name,
                "description": plan.description,
                "price_usd": plan.price_usd,
                "unit_amount_minor": plan.unit_amount_minor,
                "currency": plan.currency,
                "interval": plan.interval,
                "interval_count": plan.interval_count,
                "stripe_price_id": plan.stripe_price_id,
            }
        )

    sub_query = (
        db.query(UserSubscription, User, SubscriptionPlan)
        .join(User, User.account_id == UserSubscription.user_id)
        .join(SubscriptionPlan, SubscriptionPlan.id == UserSubscription.plan_id)
    )

    if plan_id:
        sub_query = sub_query.filter(UserSubscription.plan_id == plan_id)
    elif price_usd:
        sub_query = sub_query.filter(
            (SubscriptionPlan.price_usd == price_usd)
            | (SubscriptionPlan.unit_amount_minor == int(price_usd * 100))
        )

    if user_id:
        sub_query = sub_query.filter(UserSubscription.user_id == user_id)

    subscriptions = (
        sub_query.order_by(UserSubscription.id).offset(sub_skip).limit(sub_limit).all()
    )

    for sub, user, plan in subscriptions:
        result["subscriptions"].append(
            {
                "user_id": sub.user_id,
                "username": user.username if user else None,
                "subscription_id": sub.id,
                "plan_id": sub.plan_id,
                "plan_name": plan.name if plan else None,
                "plan_price_usd": plan.price_usd if plan else None,
                "status": sub.status,
                "current_period_start": (
                    sub.current_period_start.isoformat()
                    if sub.current_period_start
                    else None
                ),
                "current_period_end": (
                    sub.current_period_end.isoformat()
                    if sub.current_period_end
                    else None
                ),
                "is_active": sub.status == "active"
                and (
                    sub.current_period_end is None
                    or sub.current_period_end > datetime.utcnow()
                ),
            }
        )

    return result


def create_subscription_plan(db: Session, request):
    unit_amount_minor = request.unit_amount_minor
    if unit_amount_minor is None:
        unit_amount_minor = int(request.price_usd * 100)

    billing_interval = request.billing_interval or request.interval

    existing = (
        db.query(SubscriptionPlan)
        .filter(
            (SubscriptionPlan.unit_amount_minor == unit_amount_minor)
            | (SubscriptionPlan.price_usd == request.price_usd),
            SubscriptionPlan.interval == request.interval,
        )
        .first()
    )

    if existing:
        return {
            "success": False,
            "message": f"Subscription plan with price ${request.price_usd} and interval {request.interval} already exists (ID: {existing.id})",
            "plan_id": existing.id,
            "plan": {
                "id": existing.id,
                "name": existing.name,
                "price_usd": existing.price_usd,
                "unit_amount_minor": existing.unit_amount_minor,
                "interval": existing.interval,
            },
        }

    plan = SubscriptionPlan(
        name=request.name,
        description=request.description
        or f"{request.name} - ${request.price_usd:.2f} per {request.interval}",
        price_usd=request.price_usd,
        billing_interval=billing_interval,
        unit_amount_minor=unit_amount_minor,
        currency=request.currency,
        interval=request.interval,
        interval_count=request.interval_count,
        stripe_price_id=request.stripe_price_id,
        livemode=request.livemode,
    )

    db.add(plan)
    db.commit()
    db.refresh(plan)

    return {
        "success": True,
        "message": "Subscription plan created successfully",
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "description": plan.description,
            "price_usd": plan.price_usd,
            "unit_amount_minor": plan.unit_amount_minor,
            "currency": plan.currency,
            "interval": plan.interval,
            "interval_count": plan.interval_count,
            "stripe_price_id": plan.stripe_price_id,
        },
    }


def create_subscription_for_user(db: Session, request, current_user: User):
    user_id = request.user_id
    if user_id is None:
        user_id = current_user.account_id

    plan = (
        db.query(SubscriptionPlan)
        .filter(SubscriptionPlan.id == request.plan_id)
        .first()
    )
    if not plan:
        raise HTTPException(
            status_code=404,
            detail=f"Subscription plan with ID {request.plan_id} not found",
        )

    user = db.query(User).filter(User.account_id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User with ID {user_id} not found")

    existing = (
        db.query(UserSubscription)
        .filter(
            UserSubscription.user_id == user_id,
            UserSubscription.plan_id == plan.id,
            UserSubscription.status == "active",
        )
        .first()
    )

    if existing:
        return {
            "success": False,
            "message": f'User already has an active subscription for plan "{plan.name}" (ID: {existing.id})',
            "subscription_id": existing.id,
        }

    now = datetime.utcnow()
    period_end = now + timedelta(days=30)

    subscription = UserSubscription(
        user_id=user_id,
        plan_id=plan.id,
        status="active",
        current_period_start=now,
        current_period_end=period_end,
        livemode=False,
    )

    db.add(subscription)
    db.commit()
    db.refresh(subscription)

    return {
        "success": True,
        "message": f"Active subscription created for user {user_id}",
        "subscription": {
            "id": subscription.id,
            "user_id": subscription.user_id,
            "username": user.username,
            "plan_id": subscription.plan_id,
            "plan_name": plan.name,
            "plan_price_usd": plan.price_usd,
            "status": subscription.status,
            "current_period_start": (
                subscription.current_period_start.isoformat()
                if subscription.current_period_start
                else None
            ),
            "current_period_end": (
                subscription.current_period_end.isoformat()
                if subscription.current_period_end
                else None
            ),
        },
    }


def create_gem_package(db: Session, package):
    new_package = GemPackageConfig(
        price_minor=package.price_minor,
        gems_amount=package.gems_amount,
        is_one_time=package.is_one_time,
        description=package.description,
        bucket=package.bucket,
        object_key=package.object_key,
        mime_type=package.mime_type,
    )

    db.add(new_package)
    db.commit()
    db.refresh(new_package)

    signed_url = None
    if new_package.bucket and new_package.object_key:
        try:
            signed_url = presign_get(
                new_package.bucket, new_package.object_key, expires=900
            )
        except Exception as exc:
            logging.error(
                f"Failed to presign gem package {new_package.id}: {exc}", exc_info=True
            )

    return {
        "id": new_package.id,
        "price_usd": new_package.price_usd,
        "gems_amount": new_package.gems_amount,
        "is_one_time": new_package.is_one_time,
        "description": new_package.description,
        "url": signed_url,
        "mime_type": new_package.mime_type,
        "created_at": new_package.created_at,
        "updated_at": new_package.updated_at,
    }


def update_gem_package(db: Session, package_id: int, package):
    db_package = (
        db.query(GemPackageConfig).filter(GemPackageConfig.id == package_id).first()
    )
    if not db_package:
        raise HTTPException(
            status_code=404, detail=f"Gem package with ID {package_id} not found"
        )

    db_package.price_minor = package.price_minor
    db_package.gems_amount = package.gems_amount
    db_package.is_one_time = package.is_one_time
    db_package.description = package.description
    db_package.bucket = package.bucket
    db_package.object_key = package.object_key
    db_package.mime_type = package.mime_type
    db_package.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(db_package)

    signed_url = None
    if db_package.bucket and db_package.object_key:
        try:
            signed_url = presign_get(
                db_package.bucket, db_package.object_key, expires=900
            )
        except Exception as exc:
            logging.error(
                f"Failed to presign gem package {db_package.id}: {exc}", exc_info=True
            )

    return {
        "id": db_package.id,
        "price_usd": db_package.price_usd,
        "gems_amount": db_package.gems_amount,
        "is_one_time": db_package.is_one_time,
        "description": db_package.description,
        "url": signed_url,
        "mime_type": db_package.mime_type,
        "created_at": db_package.created_at,
        "updated_at": db_package.updated_at,
    }


def delete_gem_package(db: Session, package_id: int):
    db_package = (
        db.query(GemPackageConfig).filter(GemPackageConfig.id == package_id).first()
    )
    if not db_package:
        raise HTTPException(
            status_code=404, detail=f"Gem package with ID {package_id} not found"
        )

    db.delete(db_package)
    db.commit()
    return {"message": f"Gem package with ID {package_id} deleted successfully"}


def validate_badge_url_is_public(image_url: str) -> bool:
    if not image_url:
        return False
    presigned_indicators = [
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Signature",
        "X-Amz-Date",
    ]
    if any(indicator in image_url for indicator in presigned_indicators):
        logging.warning(
            f"Badge URL appears to be presigned (should be public): {image_url[:100]}..."
        )
        return False
    public_url_patterns = [
        "s3.amazonaws.com",
        "s3.",
        "amazonaws.com",
        "cdn.",
        ".com/",
        ".org/",
    ]
    if any(pattern in image_url for pattern in public_url_patterns):
        return True
    if image_url.startswith("http://") or image_url.startswith("https://"):
        return True
    return False


def create_badge(db: Session, badge):
    if not validate_badge_url_is_public(badge.image_url):
        logging.warning(
            f"Creating badge with URL that appears non-public: {badge.image_url[:100]}. "
            f"Badges should use public S3 URLs for optimal performance."
        )

    badge_id = badge.id if badge.id else str(uuid.uuid4())

    if badge.id:
        existing = (
            db.query(TriviaModeConfig)
            .filter(TriviaModeConfig.mode_id == badge_id)
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Mode config with ID {badge_id} already exists. Use PUT to update badge fields.",
            )

    new_mode_config = TriviaModeConfig(
        mode_id=badge_id,
        mode_name=badge.name,
        questions_count=1,
        reward_distribution="{}",
        amount=0.0,
        leaderboard_types="[]",
        badge_image_url=badge.image_url,
        badge_description=badge.description,
        badge_level=badge.level,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    db.add(new_mode_config)
    db.commit()
    db.refresh(new_mode_config)

    logging.info(
        f"Created badge {badge_id} ({badge.name}) with public URL: {badge.image_url[:80]}..."
    )

    return {
        "id": new_mode_config.mode_id,
        "name": new_mode_config.mode_name,
        "description": new_mode_config.badge_description,
        "image_url": new_mode_config.badge_image_url,
        "level": new_mode_config.badge_level or 0,
        "created_at": new_mode_config.created_at,
    }


def update_badge(db: Session, badge_id: str, badge_update):
    mode_config = (
        db.query(TriviaModeConfig).filter(TriviaModeConfig.mode_id == badge_id).first()
    )
    if not mode_config:
        raise HTTPException(
            status_code=404, detail=f"Badge with ID {badge_id} not found"
        )

    if not validate_badge_url_is_public(badge_update.image_url):
        logging.warning(
            f"Updating badge {badge_id} with URL that appears non-public: {badge_update.image_url[:100]}. "
            f"Badges should use public S3 URLs for optimal performance."
        )

    mode_config.mode_name = badge_update.name
    mode_config.badge_description = badge_update.description
    mode_config.badge_image_url = badge_update.image_url
    mode_config.badge_level = badge_update.level
    mode_config.updated_at = datetime.utcnow()

    users_updated = db.query(User).filter(User.badge_id == badge_id).count()

    db.commit()
    db.refresh(mode_config)

    logging.info(
        f"Updated badge {badge_id} ({badge_update.name}). "
        f"Image URL changed, {users_updated} users updated with new badge image URL."
    )

    return {
        "id": mode_config.mode_id,
        "name": mode_config.mode_name,
        "description": mode_config.badge_description,
        "image_url": mode_config.badge_image_url,
        "level": mode_config.badge_level or 0,
        "created_at": mode_config.created_at,
    }


def get_badge_assignments(db: Session):
    result = {}
    counts = dict(
        db.query(User.badge_id, func.count(User.account_id))
        .group_by(User.badge_id)
        .all()
    )
    badges = (
        db.query(TriviaModeConfig)
        .filter(TriviaModeConfig.badge_image_url.isnot(None))
        .all()
    )

    for mode_config in badges:
        result[mode_config.mode_id] = {
            "badge_name": mode_config.mode_name,
            "user_count": counts.get(mode_config.mode_id, 0),
        }

    no_badge_count = counts.get(None, 0)
    result["no_badge"] = {"badge_name": "No Badge", "user_count": no_badge_count}

    return {"assignments": result, "total_users": sum(counts.values())}


def create_avatar(db: Session, avatar):
    avatar_id = avatar.id if avatar.id else str(uuid.uuid4())
    if avatar.id:
        existing = db.query(Avatar).filter(Avatar.id == avatar_id).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Avatar with ID {avatar_id} already exists",
            )

    new_avatar = Avatar(
        id=avatar_id,
        name=avatar.name,
        description=avatar.description,
        price_gems=avatar.price_gems,
        price_usd=avatar.price_usd,
        is_premium=avatar.is_premium,
        bucket=avatar.bucket,
        object_key=avatar.object_key,
        mime_type=avatar.mime_type,
        created_at=datetime.utcnow(),
    )
    db.add(new_avatar)
    db.commit()
    db.refresh(new_avatar)
    return new_avatar


def update_avatar(db: Session, avatar_id: str, avatar_update):
    avatar = db.query(Avatar).filter(Avatar.id == avatar_id).first()
    if not avatar:
        raise HTTPException(
            status_code=404, detail=f"Avatar with ID {avatar_id} not found"
        )

    avatar.name = avatar_update.name
    avatar.description = avatar_update.description
    avatar.price_gems = avatar_update.price_gems
    avatar.price_minor = avatar_update.price_minor
    avatar.is_premium = avatar_update.is_premium
    avatar.bucket = avatar_update.bucket
    avatar.object_key = avatar_update.object_key
    avatar.mime_type = avatar_update.mime_type

    db.commit()
    db.refresh(avatar)
    return avatar


def delete_avatar(db: Session, avatar_id: str):
    avatar = db.query(Avatar).filter(Avatar.id == avatar_id).first()
    if not avatar:
        raise HTTPException(
            status_code=404, detail=f"Avatar with ID {avatar_id} not found"
        )

    db.query(UserAvatar).filter(UserAvatar.avatar_id == avatar_id).delete(
        synchronize_session=False
    )
    db.query(User).filter(User.selected_avatar_id == avatar_id).update(
        {User.selected_avatar_id: None}, synchronize_session=False
    )
    db.delete(avatar)
    db.commit()
    return {
        "status": "success",
        "message": f"Avatar with ID {avatar_id} deleted successfully",
    }


def create_frame(db: Session, frame):
    frame_id = frame.id if frame.id else str(uuid.uuid4())
    if frame.id:
        existing = db.query(Frame).filter(Frame.id == frame_id).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Frame with ID {frame_id} already exists",
            )

    new_frame = Frame(
        id=frame_id,
        name=frame.name,
        description=frame.description,
        price_gems=frame.price_gems,
        price_usd=frame.price_usd,
        is_premium=frame.is_premium,
        bucket=frame.bucket,
        object_key=frame.object_key,
        mime_type=frame.mime_type,
        created_at=datetime.utcnow(),
    )
    db.add(new_frame)
    db.commit()
    db.refresh(new_frame)
    return new_frame


def update_frame(db: Session, frame_id: str, frame_update):
    frame = db.query(Frame).filter(Frame.id == frame_id).first()
    if not frame:
        raise HTTPException(
            status_code=404, detail=f"Frame with ID {frame_id} not found"
        )

    frame.name = frame_update.name
    frame.description = frame_update.description
    frame.price_gems = frame_update.price_gems
    frame.price_minor = frame_update.price_minor
    frame.is_premium = frame_update.is_premium
    frame.bucket = frame_update.bucket
    frame.object_key = frame_update.object_key
    frame.mime_type = frame_update.mime_type

    db.commit()
    db.refresh(frame)
    return frame


def delete_frame(db: Session, frame_id: str):
    frame = db.query(Frame).filter(Frame.id == frame_id).first()
    if not frame:
        raise HTTPException(
            status_code=404, detail=f"Frame with ID {frame_id} not found"
        )

    db.query(UserFrame).filter(UserFrame.frame_id == frame_id).delete(
        synchronize_session=False
    )
    db.query(User).filter(User.selected_frame_id == frame_id).update(
        {User.selected_frame_id: None}, synchronize_session=False
    )
    db.delete(frame)
    db.commit()
    return {
        "status": "success",
        "message": f"Frame with ID {frame_id} deleted successfully",
    }


def import_avatars_from_json(db: Session, json_data: Dict[str, Any]):
    if "avatars" in json_data:
        avatars = json_data.get("avatars", [])
    elif "id" in json_data and "name" in json_data:
        avatars = [json_data]
    else:
        avatars = []

    if not avatars:
        return {
            "status": "error",
            "message": "No avatars found in the JSON data",
            "imported_count": 0,
        }

    imported = 0
    errors = []

    for avatar_data in avatars:
        try:
            avatar_id = avatar_data.get("id", str(uuid.uuid4()))
            existing = db.query(Avatar).filter(Avatar.id == avatar_id).first()
            if existing:
                for key, value in avatar_data.items():
                    if key != "id" and hasattr(existing, key):
                        setattr(existing, key, value)
            else:
                new_avatar = Avatar(
                    id=avatar_id,
                    name=avatar_data.get("name", "Unnamed Avatar"),
                    description=avatar_data.get("description"),
                    price_gems=avatar_data.get("price_gems"),
                    price_usd=avatar_data.get("price_usd"),
                    is_premium=avatar_data.get("is_premium", False),
                    bucket=avatar_data.get("bucket"),
                    object_key=avatar_data.get("object_key"),
                    mime_type=avatar_data.get("mime_type"),
                    created_at=datetime.utcnow(),
                )
                db.add(new_avatar)
            imported += 1
        except Exception:
            name = avatar_data.get("name", "unknown")
            logging.error(f"Error importing avatar {name}", exc_info=True)
            errors.append(f"Error importing avatar {name}")

    try:
        db.commit()
    except Exception:
        db.rollback()
        logging.error("Database error while importing avatars", exc_info=True)
        return {
            "status": "error",
            "message": "Database error",
            "imported_count": 0,
            "errors": ["Database error"],
        }

    return {
        "status": "success",
        "message": f"Successfully imported {imported} avatars",
        "imported_count": imported,
        "errors": errors,
    }


def import_frames_from_json(db: Session, json_data: Dict[str, Any]):
    if "frames" in json_data:
        frames = json_data.get("frames", [])
    elif "id" in json_data and "name" in json_data:
        frames = [json_data]
    else:
        frames = []

    if not frames:
        return {
            "status": "error",
            "message": "No frames found in the JSON data",
            "imported_count": 0,
        }

    imported = 0
    errors = []

    for frame_data in frames:
        try:
            frame_id = frame_data.get("id", str(uuid.uuid4()))
            existing = db.query(Frame).filter(Frame.id == frame_id).first()
            if existing:
                for key, value in frame_data.items():
                    if key != "id" and hasattr(existing, key):
                        setattr(existing, key, value)
            else:
                new_frame = Frame(
                    id=frame_id,
                    name=frame_data.get("name", "Unnamed Frame"),
                    description=frame_data.get("description"),
                    price_gems=frame_data.get("price_gems"),
                    price_usd=frame_data.get("price_usd"),
                    is_premium=frame_data.get("is_premium", False),
                    bucket=frame_data.get("bucket"),
                    object_key=frame_data.get("object_key"),
                    mime_type=frame_data.get("mime_type"),
                    created_at=datetime.utcnow(),
                )
                db.add(new_frame)
            imported += 1
        except Exception:
            name = frame_data.get("name", "unknown")
            logging.error(f"Error importing frame {name}", exc_info=True)
            errors.append(f"Error importing frame {name}")

    try:
        db.commit()
    except Exception:
        db.rollback()
        logging.error("Database error while importing frames", exc_info=True)
        return {
            "status": "error",
            "message": "Database error",
            "imported_count": 0,
            "errors": ["Database error"],
        }

    return {
        "status": "success",
        "message": f"Successfully imported {imported} frames",
        "imported_count": imported,
        "errors": errors,
    }


def get_avatar_stats(db: Session):
    total_avatars = db.query(Avatar).count()
    default_avatars = db.query(Avatar).filter(Avatar.is_default == True).count()
    premium_avatars = db.query(Avatar).filter(Avatar.is_premium == True).count()

    free_avatars = (
        db.query(Avatar)
        .filter(Avatar.price_gems.is_(None), Avatar.price_usd.is_(None))
        .count()
    )

    gem_purchasable = db.query(Avatar).filter(Avatar.price_gems.isnot(None)).count()
    usd_purchasable = db.query(Avatar).filter(Avatar.price_usd.isnot(None)).count()

    top_avatars = (
        db.query(
            Avatar.id,
            Avatar.name,
            func.count(UserAvatar.avatar_id).label("purchase_count"),
        )
        .join(UserAvatar, UserAvatar.avatar_id == Avatar.id)
        .group_by(Avatar.id, Avatar.name)
        .order_by(func.desc("purchase_count"))
        .limit(5)
        .all()
    )

    top_avatars_data = [
        {"id": avatar.id, "name": avatar.name, "purchase_count": avatar.purchase_count}
        for avatar in top_avatars
    ]

    return {
        "total_avatars": total_avatars,
        "default_avatars": default_avatars,
        "premium_avatars": premium_avatars,
        "free_avatars": free_avatars,
        "gem_purchasable": gem_purchasable,
        "usd_purchasable": usd_purchasable,
        "top_avatars": top_avatars_data,
    }


def list_users(db: Session, skip: int, limit: int):
    return auth_repository.get_users_paginated(db, skip, limit)


def update_user_admin_status(db: Session, account_id: int, is_admin: bool):
    user = auth_repository.get_user_by_account_id(db, account_id)
    if not user:
        raise HTTPException(
            status_code=404, detail=f"User with account ID {account_id} not found"
        )
    user.is_admin = is_admin
    db.commit()
    db.refresh(user)
    message = (
        f"User {user.email} is now {'an admin' if user.is_admin else 'not an admin'}"
    )
    return {
        "account_id": user.account_id,
        "email": user.email,
        "username": user.username,
        "is_admin": user.is_admin,
        "message": message,
    }


def search_users(
    db: Session,
    email: Optional[str],
    username: Optional[str],
    is_admin: Optional[bool],
    contains: bool,
    skip: int,
    limit: int,
):
    return auth_repository.search_users(
        db, email, username, is_admin, contains, skip, limit
    )
