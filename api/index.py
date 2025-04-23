from fastapi import FastAPI
from fastapi.responses import JSONResponse
import sys
import os

# Add the parent directory to the path so we can import from the root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the main app
try:
    from main import app
except ImportError as e:
    app = FastAPI()
    
    @app.get("/")
    def read_root():
        return {"error": f"Failed to import main application: {str(e)}"}

# Export the app for Vercel serverless
handler = app 