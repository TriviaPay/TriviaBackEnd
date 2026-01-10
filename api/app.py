import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Create a FastAPI application
app = FastAPI(
    title="TriviaPay API",
    description="Backend API for TriviaPay application",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def read_root():
    """
    Root endpoint to check if the server is running.
    """
    return {"status": "online", "message": "TriviaPay Backend API", "version": "1.0.0"}


@app.get("/health")
async def health_check():
    """
    Health check endpoint for monitoring.
    """
    return {"status": "healthy", "python_version": sys.version}


# This is needed for Vercel serverless function
handler = app
