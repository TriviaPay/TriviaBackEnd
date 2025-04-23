import sys
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Print environment info
print("🧪 STANDALONE MODE ACTIVATED 🧪")
print(f"Python version: {sys.version}")

try:
    import pydantic
    print(f"🧪 Pydantic version: {pydantic.__version__}")
except ImportError as e:
    print(f"❌ Failed to import pydantic: {str(e)}")

try:
    import fastapi
    print(f"🧪 FastAPI version: {fastapi.__version__}")
except ImportError as e:
    print(f"❌ Failed to import fastapi: {str(e)}")

# Create a standalone app for Vercel
app = FastAPI(
    title="TriviaPay API",
    description="Backend API for TriviaPay application (Vercel Deployment)",
    version="1.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"]
)

@app.get("/")
async def read_root():
    """
    Root endpoint to check if the server is running.
    Returns basic API information.
    """
    return {
        "status": "online",
        "message": "TriviaPay Backend - Vercel Deployment",
        "version": "1.0.0",
        "environment": os.getenv("APP_ENV", "production")
    }

@app.get("/health")
async def health_check():
    """
    Health check endpoint for monitoring.
    """
    return {
        "status": "healthy",
        "deployment": "vercel",
        "python_version": sys.version,
        "fastapi_version": fastapi.__version__,
        "pydantic_version": pydantic.__version__
    }

# Export the app as ASGI application
# This is what Vercel expects
handler = app 