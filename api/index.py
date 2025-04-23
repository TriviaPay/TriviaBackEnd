from fastapi import FastAPI
import sys

# Print versions for debugging
print(f"Python version: {sys.version}")
print(f"Type of FastAPI: {type(FastAPI)}")

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

# Export the FastAPI app directly as the handler
# This is what Vercel expects
handler = app 