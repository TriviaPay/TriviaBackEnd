import io
import logging
import os
import sys

# Suppress warnings BEFORE loading dotenv
import warnings

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException

# entries router removed - legacy /entries endpoint deleted
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi

from routers.auth import router as auth_router
from routers.app_versions import router as app_versions_router
from routers.messaging import router as messaging_router
from routers.notifications import router as notifications_router
from routers.support import router as support_router
from routers.store import router as store_router
from routers.trivia import router as trivia_router

# Conditional scheduler for local development


# Suppress ALL dotenv-related warnings
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*dotenv.*")
warnings.filterwarnings("ignore", message=".*Python-dotenv.*")


# Create a filter for stderr that removes dotenv warnings
class FilteredStderr:
    def __init__(self, original_stderr):
        self.original_stderr = original_stderr

    def write(self, text):
        # Filter out dotenv-related warnings
        if text and ("dotenv" not in text.lower() and "Python-dotenv" not in text):
            self.original_stderr.write(text)

    def flush(self):
        self.original_stderr.flush()

    def __getattr__(self, name):
        return getattr(self.original_stderr, name)


# Redirect stderr to filter out dotenv warnings (only if not already filtered)
if not isinstance(sys.stderr, FilteredStderr):
    _filtered_stderr = FilteredStderr(sys.stderr)
    sys.stderr = _filtered_stderr

# Load environment variables
from dotenv import load_dotenv

load_dotenv(override=False)

# Configure logging
import logging
import sys
import uuid
from contextvars import ContextVar

from core.config import ENVIRONMENT, LOG_LEVEL
from core.logging import configure_logging, request_id_var

log_level = configure_logging(environment=ENVIRONMENT, log_level=LOG_LEVEL)
logger = logging.getLogger(__name__)

# Suppress importlib.metadata warnings (Python 3.9 compatibility)
warnings.filterwarnings("ignore", message=".*importlib.metadata.*")
warnings.filterwarnings("ignore", category=FutureWarning, message=".*Python version.*")
warnings.filterwarnings(
    "ignore", category=FutureWarning, message=".*You are using a Python version.*"
)

# Suppress importlib.metadata errors by catching AttributeError
try:
    import importlib.metadata

    if not hasattr(importlib.metadata, "packages_distributions"):
        # Add a dummy attribute to prevent errors
        importlib.metadata.packages_distributions = lambda: {}
except (AttributeError, ImportError):
    pass

# Import async wallet routers
from app.routers.payments import router as payments_router

# Initialize FastAPI app
app = FastAPI(
    title="TriviaPay API",
    description="Unified Backend API for TriviaPay application - Includes trivia, rewards, wallet, payments, and chat features",
    version="1.0.0",
    swagger_ui_parameters={
        "defaultModelsExpandDepth": -1,
        "defaultModelExpandDepth": -1,
        "docExpansion": "none",
        "syntaxHighlight.activate": True,
        "tryItOutEnabled": False,  # Disable try it out button
        "persistAuthorization": False,  # Don't persist authorization
        "displayRequestDuration": True,
        "filter": True,
        "deepLinking": True,
        "displayOperationId": True,
        "defaultModelsExpandDepth": 2,
        "defaultModelExpandDepth": 2,
    },
)


# Add security scheme
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title="TriviaPay API",
        version="1.0.0",
        description="""
        TriviaPay Backend API

        ## Authentication
        This API requires a Bearer JWT token for authentication.
        All endpoints except /login and /auth/refresh require a valid access token.

        Format: `Authorization: Bearer <your_access_token>`
        """,
        routes=app.routes,
    )
    openapi_schema["components"]["securitySchemes"] = {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
    }
    # Apply security globally to all routes except login and refresh
    openapi_schema["security"] = [{"bearerAuth": []}]
    app.openapi_schema = openapi_schema
    return openapi_schema


app.openapi = custom_openapi

import time

# Request logging middleware with request ID tracking
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from core.latency import LatencyTracker

_latency_tracker = LatencyTracker(
    window=int(os.getenv("LATENCY_STATS_WINDOW", "200"))
)
_last_latency_log_epoch = 0.0


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    DOMAIN_PREFIXES = (
        ("auth", ("/login", "/auth/refresh", "/profile", "/admin")),
        (
            "trivia",
            (
                "/trivia",
                "/trivia-free",
                "/trivia-five-dollar",
                "/trivia-silver",
                "/trivia-live-chat",
                "/draw",
                "/rewards",
                "/internal",
            ),
        ),
        ("store", ("/store", "/cosmetics", "/badges")),
        (
            "messaging",
            (
                "/global-chat",
                "/private-chat",
                "/dm",
                "/group",
                "/groups",
                "/status",
                "/presence",
                "/chat-mute",
                "/e2ee",
            ),
        ),
        ("notifications", ("/notifications", "/onesignal", "/pusher-auth")),
        (
            "payments",
            (
                "/api/v1/wallet",
                "/api/v1/iap",
            ),
        ),
    )

    def _get_domain(self, path: str) -> str:
        for domain, prefixes in self.DOMAIN_PREFIXES:
            if any(path.startswith(prefix) for prefix in prefixes):
                return domain
        return "unknown"

    async def dispatch(self, request: Request, call_next):
        # Generate unique request ID for tracking
        request_id = str(uuid.uuid4())[:8]  # Short ID for readability
        request_id_var.set(request_id)
        request.state.request_id = request_id

        start_time = time.time()
        domain = self._get_domain(request.url.path)
        slow_ms = float(os.getenv("SLOW_REQUEST_THRESHOLD_MS", "500"))
        slow_log_stack = os.getenv("SLOW_REQUEST_LOG_STACK", "false").lower() == "true"

        # Extract user info if available (from auth header)
        user_id = None
        auth_header = request.headers.get("authorization") or request.headers.get(
            "Authorization"
        )
        if auth_header:
            try:
                from auth import validate_descope_jwt

                token = (
                    auth_header.split(" ", 1)[1].strip()
                    if " " in auth_header
                    else auth_header
                )
                user_info = validate_descope_jwt(token)
                user_id = user_info.get("userId", "unknown")[:8] if user_info else None
            except:
                pass  # Don't fail if we can't extract user ID

        # Log incoming request with context
        query_str = f"?{request.url.query}" if request.query_params else ""
        logger.info(
            f"REQUEST | id={request_id} | method={request.method} | path={request.url.path}{query_str} | "
            f"domain={domain} | user_id={user_id or 'anonymous'} | ip={request.client.host if request.client else 'unknown'}"
        )

        if request.query_params and log_level <= logging.DEBUG:
            logger.debug(
                f"QUERY_PARAMS | id={request_id} | params={dict(request.query_params)}"
            )

        try:
            response = await call_next(request)
            process_time = time.time() - start_time
            elapsed_ms = process_time * 1000.0
            # Key by domain + path (good enough for top-N guidance).
            _latency_tracker.record(
                key=f"{domain} {request.method} {request.url.path}",
                elapsed_ms=elapsed_ms,
            )

            # Log response with context
            status_emoji = (
                "✅"
                if 200 <= response.status_code < 300
                else "⚠️" if 300 <= response.status_code < 400 else "❌"
            )
            logger.info(
                f"RESPONSE | id={request_id} | method={request.method} | path={request.url.path} | "
                f"status={response.status_code} | time={process_time:.3f}s | domain={domain} | user_id={user_id or 'anonymous'}"
            )

            global _last_latency_log_epoch
            interval_s = float(os.getenv("LATENCY_LOG_INTERVAL_SECONDS", "300"))
            top_n = int(os.getenv("LATENCY_TOP_N", "5"))
            if interval_s > 0 and (time.time() - _last_latency_log_epoch) >= interval_s:
                _last_latency_log_epoch = time.time()
                top = _latency_tracker.top(n=top_n, metric="p95")
                if top:
                    lines = [
                        f"{s.key} count={s.count} p50={s.p50_ms:.1f}ms p95={s.p95_ms:.1f}ms max={s.max_ms:.1f}ms"
                        for s in top
                    ]
                    logger.info("LATENCY_TOP | " + " | ".join(lines))

            if elapsed_ms >= slow_ms:
                extra = {
                    "id": request_id,
                    "domain": domain,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": round(elapsed_ms, 1),
                }
                if request.query_params:
                    extra["query_params"] = dict(request.query_params)
                logger.warning(f"SLOW_REQUEST | {extra}")
                if slow_log_stack:
                    import traceback

                    logger.warning(
                        "SLOW_REQUEST_STACK | id=%s | %s",
                        request_id,
                        "".join(traceback.format_stack(limit=30)).strip(),
                    )

            if response.status_code in {403, 404}:
                detail_snippet = ""
                if hasattr(response, "body"):
                    body = getattr(response, "body")
                    if isinstance(body, (bytes, str)) and body:
                        snippet = (
                            body.decode("utf-8", errors="ignore")
                            if isinstance(body, bytes)
                            else body
                        )
                        detail_snippet = snippet.strip().replace("\n", " ")[:200]
                logger.warning(
                    f"CLIENT_ERROR | id={request_id} | status={response.status_code} | "
                    f"path={request.url.path} | domain={domain} | user_id={user_id or 'anonymous'} | detail={detail_snippet or 'no detail'}"
                )

            # Add request ID to response headers for client tracking
            response.headers["X-Request-ID"] = request_id

            return response
        except Exception as e:
            process_time = time.time() - start_time
            logger.error(
                f"ERROR | id={request_id} | method={request.method} | path={request.url.path} | "
                f"error={type(e).__name__}: {str(e)} | time={process_time:.3f}s | domain={domain} | user_id={user_id or 'anonymous'}",
                exc_info=True,
            )
            raise


# Add request logging middleware (before CORS so it logs all requests)
app.add_middleware(RequestLoggingMiddleware)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # allow_origins=["http://localhost:3000", "http://localhost:5173", "https://*.vercel.app", "http://localhost"],  # Common development and deployment URLs
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=[
        "*",
        "Authorization",
        "Content-Type",
        "Accept",
        "Origin",
        "X-Requested-With",
    ],
)

# NOTE: SSE (Server-Sent Events) streams must NOT be compressed.
# FastAPI doesn't auto-compress by default, but if you add compression middleware
# (e.g., GZipMiddleware), ensure it excludes 'text/event-stream' content type.
# Compression breaks SSE streaming because proxies/browsers buffer compressed responses.
#
# Timeout requirements for SSE:
# - Uvicorn: Set --timeout-keep-alive to at least 300s (default is 5s, too short for SSE)
# - Nginx: Set proxy_read_timeout to at least 3600s (default is 60s)
# - Redis: Ensure connection timeout is higher than SSE heartbeat interval

# Include only required routers
app.include_router(auth_router)  # Auth/Profile domain
app.include_router(app_versions_router)  # App Versions
app.include_router(trivia_router)  # Trivia/Draws/Rewards domain
app.include_router(store_router)  # Store/Cosmetics domain
app.include_router(messaging_router)  # Messaging/Realtime domain
app.include_router(notifications_router)  # Notifications domain
app.include_router(support_router)  # Support domain
# entries.router removed - legacy /entries endpoint deleted

# E2EE DM routers - REMOVED (tables and models deleted)
# All E2EE DM functionality has been removed from the codebase

# Groups routers - REMOVED (tables and models deleted)
# All Groups functionality has been removed from the codebase

# Status routers - REMOVED (tables and models deleted)
# All Status functionality has been removed from the codebase


@app.on_event("startup")
async def startup_event():
    """Start the scheduler when the application starts (local dev only)"""
    logger.info("TriviaPay API started successfully")

    # Log all registered routes for debugging
    from fastapi.routing import APIRoute

    logger.info("=== Registered Routes ===")
    for route in app.routes:
        if isinstance(route, APIRoute):
            methods = ",".join(route.methods)
            logger.info(f"{methods:8} {route.path}")
    logger.info("=== End of Routes ===")

    # Only start scheduler in local development
    if os.getenv("ENVIRONMENT", "development") == "development":
        from updated_scheduler import start_scheduler

        start_scheduler()
        logger.info("Local scheduler started")
    else:
        logger.info("Production mode - using external cron for scheduling")


@app.get("/")
async def read_root():
    """
    Root endpoint to check if the server is running.
    Returns basic API information.
    """
    return {
        "status": "online",
        "message": "Welcome to TriviaPay Backend!",
        "version": "1.0.0",
        "environment": os.getenv("APP_ENV", "development"),
    }


@app.get("/health")
async def health_check():
    """
    Health check endpoint for monitoring.
    """
    return {"status": "healthy"}


# Include async wallet routers with /api/v1 prefix
app.include_router(payments_router, prefix="/api/v1")
