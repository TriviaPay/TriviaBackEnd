"""WSGI entrypoint for deployment on Render and other platforms."""
import os
import sys
import uvicorn

# Print debugging information
print(f"Python version: {sys.version}")
print(f"Current working directory: {os.getcwd()}")
print(f"PYTHONPATH: {sys.path}")
print(f"Running as user: {os.getuid() if hasattr(os, 'getuid') else 'N/A'}")
print(f"Environment variables: PORT={os.environ.get('PORT', 'Not set')}")

# Import the FastAPI app
from main import app

if __name__ == "__main__":
    # Get port from environment variable or use default
    port = int(os.environ.get("PORT", 8000))
    
    print(f"Starting uvicorn server on port {port}")
    
    # Run the app with uvicorn (handles web server)
    uvicorn.run(
        "main:app", 
        host="0.0.0.0",  # Important: bind to all interfaces
        port=port,
        log_level="debug"
    ) 