from fastapi import FastAPI, HTTPException
from routers import draw, winners, updates, trivia, entries, login, refresh, wallet, store, profile, cosmetics, badges, rewards, admin, stripe
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from dotenv import load_dotenv
import os
import logging
import sys
from scheduler import start_scheduler


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
app.include_router(winners.router)   # Winner views
app.include_router(admin.router)     # Admin controls
app.include_router(stripe.router)    # Stripe-related endpoints

@app.on_event("startup")
async def startup_event():
    """Start the scheduler when the application starts."""
    start_scheduler()

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
