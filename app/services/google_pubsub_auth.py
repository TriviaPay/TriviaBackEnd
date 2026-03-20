"""Google Cloud Pub/Sub push authentication.

Verifies the Bearer JWT that Google Pub/Sub attaches to push webhook
requests, preventing forged refund/revocation notifications.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict

from fastapi import HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

import core.config as config

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2)


async def verify_pubsub_push_token(authorization: str | None) -> Dict:
    """Verify the Google Pub/Sub push JWT from the Authorization header.

    Args:
        authorization: Full Authorization header value (``Bearer <token>``).

    Returns:
        Decoded JWT claims on success.

    Raises:
        HTTPException(403) on any verification failure.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or invalid Authorization header",
        )

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Empty bearer token",
        )

    expected_audience = config.GOOGLE_PUBSUB_AUDIENCE
    expected_email = config.GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL

    if not expected_audience and not expected_email:
        logger.error(
            "Pub/Sub auth enabled but neither GOOGLE_PUBSUB_AUDIENCE nor "
            "GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL is configured — rejecting request"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Pub/Sub authentication not properly configured",
        )

    try:
        loop = asyncio.get_running_loop()
        claims = await loop.run_in_executor(
            _executor,
            lambda: id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                audience=expected_audience if expected_audience else None,
            ),
        )
    except Exception as exc:
        logger.warning("Google Pub/Sub token verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Pub/Sub authentication token",
        )

    # Validate email_verified claim
    if not claims.get("email_verified"):
        logger.warning("Pub/Sub token email_verified is not true")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Pub/Sub token email not verified",
        )

    # Validate service account email if configured
    if expected_email:
        token_email = claims.get("email", "")
        if token_email != expected_email:
            logger.warning(
                "Pub/Sub email mismatch: expected=%s got=%s",
                expected_email,
                token_email,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Pub/Sub service account email mismatch",
            )

    return claims
