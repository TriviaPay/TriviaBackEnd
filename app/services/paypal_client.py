"""
PayPal REST API Client.

Thin async HTTP client using httpx. Handles OAuth2 token caching.
All POST calls include PayPal-Request-Id for idempotency.
"""

import logging
import time
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class PayPalClient:
    """Async PayPal REST API client with OAuth2 token caching."""

    def __init__(self, client_id: str, client_secret: str, mode: str = "sandbox"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = (
            "https://api-m.sandbox.paypal.com"
            if mode == "sandbox"
            else "https://api-m.paypal.com"
        )
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

    async def _get_access_token(self) -> str:
        """OAuth2 client credentials grant. Caches until near expiry."""
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/v1/oauth2/token",
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "client_credentials"},
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 32400)
        return self._access_token

    async def _headers(self, request_id: Optional[str] = None) -> Dict[str, str]:
        """Build authorization headers, optionally with idempotency key."""
        token = await self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        return headers

    async def create_order(
        self,
        *,
        amount_minor: int,
        currency: str,
        reference_id: str,
        description: str,
        request_id: str,
        custom_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POST /v2/checkout/orders — intent=CAPTURE."""
        amount_str = f"{amount_minor / 100:.2f}"
        purchase_unit: Dict[str, Any] = {
            "reference_id": reference_id,
            "description": description,
            "amount": {
                "currency_code": currency.upper(),
                "value": amount_str,
            },
        }
        if custom_id:
            purchase_unit["custom_id"] = custom_id
        body = {
            "intent": "CAPTURE",
            "purchase_units": [purchase_unit],
        }
        headers = await self._headers(request_id=request_id)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/v2/checkout/orders",
                json=body,
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def capture_order(
        self, order_id: str, *, request_id: str
    ) -> Dict[str, Any]:
        """POST /v2/checkout/orders/{id}/capture."""
        headers = await self._headers(request_id=request_id)
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/v2/checkout/orders/{order_id}/capture",
                headers=headers,
                content="",  # empty body required
            )
            resp.raise_for_status()
            return resp.json()

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        """GET /v2/checkout/orders/{id} — for recovery/retry."""
        headers = await self._headers()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/v2/checkout/orders/{order_id}",
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """GET /v1/billing/subscriptions/{id}."""
        headers = await self._headers()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/v1/billing/subscriptions/{subscription_id}",
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def cancel_subscription(
        self, subscription_id: str, reason: str
    ) -> None:
        """POST /v1/billing/subscriptions/{id}/cancel."""
        headers = await self._headers()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/v1/billing/subscriptions/{subscription_id}/cancel",
                json={"reason": reason},
                headers=headers,
            )
            resp.raise_for_status()

    async def verify_webhook_signature(
        self,
        *,
        headers: Dict[str, str],
        body: bytes,
        webhook_id: str,
    ) -> bool:
        """
        POST /v1/notifications/verify-webhook-signature.
        Returns True if verification_status == 'SUCCESS'.
        """
        import json

        payload = {
            "auth_algo": headers.get("PAYPAL-AUTH-ALGO", ""),
            "cert_url": headers.get("PAYPAL-CERT-URL", ""),
            "transmission_id": headers.get("PAYPAL-TRANSMISSION-ID", ""),
            "transmission_sig": headers.get("PAYPAL-TRANSMISSION-SIG", ""),
            "transmission_time": headers.get("PAYPAL-TRANSMISSION-TIME", ""),
            "webhook_id": webhook_id,
            "webhook_event": json.loads(body),
        }
        auth_headers = await self._headers()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/v1/notifications/verify-webhook-signature",
                json=payload,
                headers=auth_headers,
            )
            resp.raise_for_status()
            result = resp.json()

        return result.get("verification_status") == "SUCCESS"

    async def get_capture(self, capture_id: str) -> Dict[str, Any]:
        """GET /v2/payments/captures/{id}."""
        headers = await self._headers()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/v2/payments/captures/{capture_id}",
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_webhook_event(self, event_id: str) -> Dict[str, Any]:
        """GET /v1/notifications/webhooks-events/{event_id} — fallback for retries."""
        headers = await self._headers()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/v1/notifications/webhooks-events/{event_id}",
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()
