from fastapi import FastAPI, HTTPException
from routers import draw, updates, trivia, entries, login, refresh, wallet, store, profile, cosmetics, badges, rewards, admin, stripe, internal, live_chat, global_chat, private_chat, trivia_live_chat, onesignal, pusher_auth
from config import E2EE_DM_ENABLED, GROUPS_ENABLED, STATUS_ENABLED, PRESENCE_ENABLED
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from dotenv import load_dotenv
import os
import logging
import sys
# Conditional scheduler for local development


# Load environment variables
load_dotenv()

# Configure logging
import logging
import sys

# Create a custom logger
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# Create handlers
c_handler = logging.StreamHandler(sys.stdout)  # Console handler
c_handler.setLevel(logging.DEBUG)

# Create formatters and add it to handlers
c_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
c_handler.setFormatter(c_format)

# Add handlers to the logger
logger.addHandler(c_handler)

# Reduce noise from other libraries
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)

# Initialize FastAPI app
app = FastAPI(
    title="TriviaPay API",
    description="Backend API for TriviaPay application",
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
app.include_router(wallet.router)    # Wallet-related endpoints
app.include_router(store.router)     # Store and purchases
app.include_router(profile.router)   # User profile management
app.include_router(cosmetics.router) # Avatars and Frames management
app.include_router(badges.router)    # Badges management
app.include_router(rewards.router)   # Rewards system
app.include_router(admin.router)     # Admin controls
app.include_router(stripe.router)    # Stripe-related endpoints
app.include_router(updates.router)   # Live updates
app.include_router(entries.router)   # Entries management
app.include_router(internal.router)  # Internal endpoints for external cron
app.include_router(live_chat.router) # Live chat endpoints
app.include_router(global_chat.router) # Global chat endpoints
app.include_router(private_chat.router) # Private chat endpoints
app.include_router(trivia_live_chat.router) # Trivia live chat endpoints
app.include_router(onesignal.router) # OneSignal endpoints
app.include_router(pusher_auth.router) # Pusher authentication endpoints

# E2EE DM routers (gated behind feature flag)
if E2EE_DM_ENABLED:
    from routers import e2ee_keys, dm_conversations, dm_messages, dm_sse, dm_privacy, dm_metrics
    app.include_router(e2ee_keys.router)        # E2EE key management
    app.include_router(dm_conversations.router) # DM conversation management
    app.include_router(dm_messages.router)      # DM message sending/receiving
    app.include_router(dm_sse.router)            # DM SSE real-time delivery
    app.include_router(dm_privacy.router)        # DM blocking/privacy
    app.include_router(dm_metrics.router)       # DM metrics (admin only)
    logger.info("E2EE DM feature enabled - routers registered")
else:
    logger.info("E2EE DM feature disabled")

# Groups routers (gated behind feature flag)
if GROUPS_ENABLED:
    from routers import groups, group_members, group_invites, group_messages, group_metrics
    app.include_router(groups.router)            # Group management
    app.include_router(group_members.router)     # Group membership
    app.include_router(group_invites.router)     # Group invites
    app.include_router(group_messages.router)    # Group messages
    app.include_router(group_metrics.router)         # Group metrics (admin only)
    logger.info("Groups feature enabled - routers registered")
else:
    logger.info("Groups feature disabled")

# Status routers (gated behind feature flag)
if STATUS_ENABLED:
    from routers import status, status_metrics
    app.include_router(status.router)             # Status posts
    app.include_router(status_metrics.router)    # Status metrics (admin only)
    logger.info("Status feature enabled - routers registered")
else:
    logger.info("Status feature disabled")

# Presence router (gated behind feature flag)
if PRESENCE_ENABLED:
    from routers import presence
    app.include_router(presence.router)          # Presence/privacy settings
    logger.info("Presence feature enabled - routers registered")
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
