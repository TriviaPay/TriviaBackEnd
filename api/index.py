from fastapi import FastAPI
from fastapi.responses import JSONResponse
import sys

# Print versions for debugging
print(f"Python version: {sys.version}")

# Create a simple FastAPI application
app = FastAPI(
    title="TriviaPay API",
    description="TriviaPay Backend API - Vercel Deployment",
    version="1.0.0"
)

print(f"✅ Type of app: {type(app)}")

@app.get("/")
async def read_root():
    return {
        "status": "online",
        "message": "TriviaPay API is working!",
        "python_version": sys.version
    }

# Define a handler for Vercel serverless functions
def handler(request, context):
    # This is a simple handler that returns a JSON response
    return JSONResponse({
        "status": "online",
        "message": "TriviaPay API is working via serverless function!",
        "python_version": str(sys.version)
    }) 