from fastapi import FastAPI, HTTPException
from routers import draw, winners, updates, trivia, entries, login, refresh
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from dotenv import load_dotenv
import os
import logging
import sys


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
        "tryItOutEnabled": True,
        "persistAuthorization": True,  # Keep authorization between requests
        "displayRequestDuration": True,  # Show request duration
        "filter": True,  # Enable filtering
        "deepLinking": True,  # Enable deep linking
        "displayOperationId": True,  # Show operation IDs
        "defaultModelsExpandDepth": 2,  # Expand models by default
        "defaultModelExpandDepth": 2,  # Expand model properties by default
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
        - This API requires a Bearer JWT token for authentication
        - Token must be obtained from Auth0
        - Format: 'Bearer <your_access_token>'
        
        ### How to Authenticate:
        1. Click the 'Authorize' button at the top of the page
        2. Enter: 'Bearer <your_access_token>'
        3. Ensure the token is valid and not expired
        4. Verify a user exists in the database with the token's email

        ### For /auth/refresh endpoint:
        1. Use the REFRESH token, not the access token
        2. Click the 'Authorize' button
        3. Enter: 'Bearer YOUR_REFRESH_TOKEN'
        4. Click 'Authorize' and then 'Close'
        5. Try the /auth/refresh endpoint
        """,
        routes=app.routes,
    )
    openapi_schema["components"]["securitySchemes"] = {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": """
            Enter your Auth0 JWT token:
            - Prefix with 'Bearer '
            - Example: 'Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...'
            - Token must have an 'email' claim
            - User with this email must exist in the database
            
            For the /auth/refresh endpoint:
            1. Use the REFRESH token, not the access token
            2. Prefix with 'Bearer '
            3. Ensure the refresh token is valid
            4. Example: 'Bearer YOUR_REFRESH_TOKEN'
            """
        }
    }
    # Apply security globally to all routes
    openapi_schema["security"] = [{"bearerAuth": []}]
    app.openapi_schema = openapi_schema
    return openapi_schema

app.openapi = custom_openapi

# CORS configuration
origins = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "Authorization", "Content-Type", "Accept"]
)

# Include routers for different functionalities
app.include_router(draw.router)      # Draw-related endpoints
app.include_router(winners.router)   # Recent winners endpoints
app.include_router(updates.router)   # Live updates endpoints
app.include_router(trivia.router)    # Trivia questions endpoints
app.include_router(entries.router)   # Entry management endpoints
app.include_router(login.router)     # Auth0 passwordless or local login
app.include_router(refresh.router)   # Refresh tokens from DB

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
