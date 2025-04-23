from fastapi import FastAPI
import sys

# Print versions for debugging
print(f"Python version: {sys.version}")

# Create a simple FastAPI application
app = FastAPI(
    title="TriviaPay API",
    description="TriviaPay Backend API - Vercel Deployment",
    version="1.0.0"
)

@app.get("/")
async def read_root():
    return {
        "status": "online",
        "message": "TriviaPay API is working!",
        "python_version": sys.version
    }

# This is the ASGI application
async def app_wrapper(scope, receive, send):
    await app(scope, receive, send)

# Export the ASGI application that Vercel expects
handler = app_wrapper 