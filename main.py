from fastapi import FastAPI, HTTPException
from routers import draw, trivia, login, refresh, store, profile, cosmetics, badges, rewards, admin, internal, global_chat, private_chat, trivia_live_chat, onesignal, pusher_auth, chat_mute, trivia_free_mode, trivia_five_dollar_mode, trivia_silver_mode, notifications
# entries router removed - legacy /entries endpoint deleted
from config import PRESENCE_ENABLED
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from dotenv import load_dotenv
import os
import logging
import sys
# Conditional scheduler for local development


# Suppress warnings BEFORE loading dotenv
import warnings
import sys
import io

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
        if text and ('dotenv' not in text.lower() and 'Python-dotenv' not in text):
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
from config import LOG_LEVEL, ENVIRONMENT

# Request ID context variable for tracking requests across the system
# This is shared with utils/logging_helpers.py
request_id_var: ContextVar[str] = ContextVar('request_id', default='')

# Convert string log level to logging constant
log_level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

# Create a custom logger
logger = logging.getLogger()
logger.setLevel(log_level)

# Create handlers
c_handler = logging.StreamHandler(sys.stdout)
c_handler.setLevel(log_level)

# Improved log format for better readability in production
# Format: [TIMESTAMP] [LEVEL] [MODULE] [REQUEST_ID] message | context
if ENVIRONMENT == "production":
    # Production format: more compact, structured
    c_format = logging.Formatter(
        '[%(asctime)s.%(msecs)03d] [%(levelname)-5s] [%(name)-20s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
else:
    # Development format: more verbose
    c_format = logging.Formatter(
        '[%(asctime)s.%(msecs)03d] [%(levelname)-5s] [%(name)-20s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

c_handler.setFormatter(c_format)

# Add handlers to the logger (only if not already added)
if not logger.handlers:
    logger.addHandler(c_handler)

# Reduce noise from other libraries
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

# Suppress importlib.metadata warnings (Python 3.9 compatibility)
warnings.filterwarnings("ignore", message=".*importlib.metadata.*")
warnings.filterwarnings("ignore", category=FutureWarning, message=".*Python version.*")
warnings.filterwarnings("ignore", category=FutureWarning, message=".*You are using a Python version.*")

# Suppress importlib.metadata errors by catching AttributeError
try:
    import importlib.metadata
    if not hasattr(importlib.metadata, 'packages_distributions'):
        # Add a dummy attribute to prevent errors
        importlib.metadata.packages_distributions = lambda: {}
except (AttributeError, ImportError):
    pass

# Configure uvicorn logging to match our log level (prevents duplicate logs)
logging.getLogger('uvicorn').setLevel(log_level)
logging.getLogger('uvicorn.error').setLevel(log_level)
logging.getLogger('uvicorn.access').setLevel(logging.WARNING)  # Suppress access logs unless needed

# Import async wallet routers
from app.routers import wallet, stripe_connect, admin_withdrawals, iap, stripe_webhook, payments

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
    }
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

# Request logging middleware with request ID tracking
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
import time

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Generate unique request ID for tracking
        request_id = str(uuid.uuid4())[:8]  # Short ID for readability
        request_id_var.set(request_id)
        request.state.request_id = request_id
        
        start_time = time.time()
        
        # Extract user info if available (from auth header)
        user_id = None
        auth_header = request.headers.get('authorization') or request.headers.get('Authorization')
        if auth_header:
            try:
                from auth import validate_descope_jwt
                token = auth_header.split(' ', 1)[1].strip() if ' ' in auth_header else auth_header
                user_info = validate_descope_jwt(token)
                user_id = user_info.get('userId', 'unknown')[:8] if user_info else None
            except:
                pass  # Don't fail if we can't extract user ID
        
        # Log incoming request with context
        query_str = f"?{request.url.query}" if request.query_params else ""
        logger.info(
            f"REQUEST | id={request_id} | method={request.method} | path={request.url.path}{query_str} | "
            f"user_id={user_id or 'anonymous'} | ip={request.client.host if request.client else 'unknown'}"
        )
        
        if request.query_params and log_level <= logging.DEBUG:
            logger.debug(f"QUERY_PARAMS | id={request_id} | params={dict(request.query_params)}")
        
        try:
            response = await call_next(request)
            process_time = time.time() - start_time
            
            # Log response with context
            status_emoji = "✅" if 200 <= response.status_code < 300 else "⚠️" if 300 <= response.status_code < 400 else "❌"
            logger.info(
                f"RESPONSE | id={request_id} | method={request.method} | path={request.url.path} | "
                f"status={response.status_code} | time={process_time:.3f}s | user_id={user_id or 'anonymous'}"
            )
            
            # Add request ID to response headers for client tracking
            response.headers["X-Request-ID"] = request_id
            
            return response
        except Exception as e:
            process_time = time.time() - start_time
            logger.error(
                f"ERROR | id={request_id} | method={request.method} | path={request.url.path} | "
                f"error={type(e).__name__}: {str(e)} | time={process_time:.3f}s | user_id={user_id or 'anonymous'}",
                exc_info=True
            )
            raise

# Add request logging middleware (before CORS so it logs all requests)
app.add_middleware(RequestLoggingMiddleware)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    #allow_origins=["http://localhost:3000", "http://localhost:5173", "https://*.vercel.app", "http://localhost"],  # Common development and deployment URLs
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"]
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
app.include_router(login.router)     # Auth0 login
app.include_router(refresh.router)   # Token refresh
app.include_router(draw.router)      # Draw-related endpoints
app.include_router(trivia.router)    # Trivia questions and lifelines
app.include_router(trivia_free_mode.router)  # Free mode trivia endpoints
app.include_router(trivia_five_dollar_mode.router)  # Bronze mode ($5) trivia endpoints
app.include_router(trivia_silver_mode.router)  # Silver mode ($10) trivia endpoints
app.include_router(store.router)     # Store and purchases
app.include_router(profile.router)   # User profile management
app.include_router(cosmetics.router) # Avatars and Frames management
app.include_router(badges.router)    # Badges management
app.include_router(rewards.router)   # Rewards system
app.include_router(admin.router)     # Admin controls
# entries.router removed - legacy /entries endpoint deleted
app.include_router(internal.router)  # Internal endpoints for external cron
app.include_router(global_chat.router) # Global chat endpoints
app.include_router(private_chat.router) # Private chat endpoints
app.include_router(trivia_live_chat.router) # Trivia live chat endpoints
app.include_router(onesignal.router) # OneSignal endpoints
app.include_router(pusher_auth.router) # Pusher authentication endpoints
app.include_router(chat_mute.router) # Chat mute preferences endpoints
app.include_router(notifications.router) # Notifications endpoints

# E2EE DM routers - REMOVED (tables and models deleted)
# All E2EE DM functionality has been removed from the codebase

# Groups routers - REMOVED (tables and models deleted)
# All Groups functionality has been removed from the codebase

# Status routers - REMOVED (tables and models deleted)
# All Status functionality has been removed from the codebase

# Presence router (gated behind feature flag)
if PRESENCE_ENABLED:
    try:
    from routers import presence
    app.include_router(presence.router)          # Presence/privacy settings
    logger.info("Presence feature enabled - routers registered")
    except ImportError as e:
        logger.warning(f"Presence feature enabled but required models are missing: {e}. Disabling Presence router.")
else:
    logger.info("Presence feature disabled")

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
        "environment": os.getenv("APP_ENV", "development")
    }

@app.get("/health")
async def health_check():
    """
    Health check endpoint for monitoring.
    """
    return {"status": "healthy"}

# Include async wallet routers with /api/v1 prefix
app.include_router(wallet.router, prefix="/api/v1")
app.include_router(stripe_connect.router, prefix="/api/v1")
app.include_router(admin_withdrawals.router, prefix="/api/v1")
app.include_router(iap.router, prefix="/api/v1")
app.include_router(stripe_webhook.router, prefix="/api/v1")
app.include_router(payments.router, prefix="/api/v1")
