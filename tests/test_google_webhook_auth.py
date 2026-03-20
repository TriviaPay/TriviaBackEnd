"""Tests for Google Pub/Sub webhook authentication."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi import HTTPException

from app.services.google_pubsub_auth import verify_pubsub_push_token


@pytest.mark.asyncio
async def test_missing_authorization_header():
    with pytest.raises(HTTPException) as exc_info:
        await verify_pubsub_push_token(None)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_empty_bearer_token():
    with pytest.raises(HTTPException) as exc_info:
        await verify_pubsub_push_token("Bearer ")
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_non_bearer_scheme():
    with pytest.raises(HTTPException) as exc_info:
        await verify_pubsub_push_token("Basic abc123")
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_invalid_token_rejected():
    with patch("app.services.google_pubsub_auth.id_token.verify_oauth2_token") as mock_verify, \
         patch("app.services.google_pubsub_auth.config") as mock_config:
        mock_config.GOOGLE_PUBSUB_AUDIENCE = "https://example.com/webhook"
        mock_config.GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL = ""
        mock_verify.side_effect = ValueError("Invalid token")
        with pytest.raises(HTTPException) as exc_info:
            await verify_pubsub_push_token("Bearer fake.jwt.token")
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_email_not_verified_rejected():
    """When email_verified is false, requests should be rejected with 403."""
    claims = {
        "email": "test@example.iam.gserviceaccount.com",
        "aud": "https://example.com/webhook",
        "email_verified": False,
    }
    with patch("app.services.google_pubsub_auth.id_token.verify_oauth2_token") as mock_verify, \
         patch("app.services.google_pubsub_auth.config") as mock_config:
        mock_verify.return_value = claims
        mock_config.GOOGLE_PUBSUB_AUDIENCE = "https://example.com/webhook"
        mock_config.GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL = "test@example.iam.gserviceaccount.com"

        with pytest.raises(HTTPException) as exc_info:
            await verify_pubsub_push_token("Bearer valid.jwt.token")
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_valid_token_accepted():
    expected_claims = {
        "email": "test@example.iam.gserviceaccount.com",
        "aud": "https://example.com/webhook",
        "iss": "accounts.google.com",
        "email_verified": True,
    }
    with patch("app.services.google_pubsub_auth.id_token.verify_oauth2_token") as mock_verify, \
         patch("app.services.google_pubsub_auth.config") as mock_config:
        mock_verify.return_value = expected_claims
        mock_config.GOOGLE_PUBSUB_AUDIENCE = "https://example.com/webhook"
        mock_config.GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL = "test@example.iam.gserviceaccount.com"

        claims = await verify_pubsub_push_token("Bearer valid.jwt.token")
        assert claims["email"] == "test@example.iam.gserviceaccount.com"


@pytest.mark.asyncio
async def test_email_mismatch_rejected():
    claims = {
        "email": "wrong@example.iam.gserviceaccount.com",
        "aud": "https://example.com/webhook",
        "email_verified": True,
    }
    with patch("app.services.google_pubsub_auth.id_token.verify_oauth2_token") as mock_verify, \
         patch("app.services.google_pubsub_auth.config") as mock_config:
        mock_verify.return_value = claims
        mock_config.GOOGLE_PUBSUB_AUDIENCE = "https://example.com/webhook"
        mock_config.GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL = "expected@example.iam.gserviceaccount.com"

        with pytest.raises(HTTPException) as exc_info:
            await verify_pubsub_push_token("Bearer valid.jwt.token")
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_empty_config_rejected():
    """When both audience and email are empty, requests should be rejected with 500."""
    with patch("app.services.google_pubsub_auth.config") as mock_config:
        mock_config.GOOGLE_PUBSUB_AUDIENCE = ""
        mock_config.GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL = ""

        with pytest.raises(HTTPException) as exc_info:
            await verify_pubsub_push_token("Bearer valid.jwt.token")
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_audience_only_config_accepted():
    """When only audience is configured (no email), token should be verified but email not checked."""
    claims = {
        "email": "any@example.iam.gserviceaccount.com",
        "aud": "https://example.com/webhook",
        "email_verified": True,
    }
    with patch("app.services.google_pubsub_auth.id_token.verify_oauth2_token") as mock_verify, \
         patch("app.services.google_pubsub_auth.config") as mock_config:
        mock_verify.return_value = claims
        mock_config.GOOGLE_PUBSUB_AUDIENCE = "https://example.com/webhook"
        mock_config.GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL = ""

        result = await verify_pubsub_push_token("Bearer valid.jwt.token")
        assert result == claims
