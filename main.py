from fastapi import FastAPI, HTTPException
from routers import draw, winners, updates, trivia, entries, login, refresh
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os


# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI(
    title="TriviaPay API",
    description="Backend API for TriviaPay application",
    version="1.0.0"
)

# CORS configuration
origins = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
