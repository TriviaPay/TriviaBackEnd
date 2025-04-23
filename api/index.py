import sys
import os

# Print environment info first thing
print("🧪 ENVIRONMENT DEBUG INFO 🧪")
print(f"Python version: {sys.version}")
print(f"Current working directory: {os.getcwd()}")
print(f"PYTHONPATH: {sys.path}")

# Now import packages that might cause issues
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

print("🧪 END DEBUG INFO 🧪")

from fastapi import FastAPI
from fastapi.responses import JSONResponse

# Add the parent directory to the path so we can import from the root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the main app
try:
    from main import app
    print("✅ Successfully imported main app")
except ImportError as e:
    print(f"❌ Failed to import main app: {str(e)}")
    app = FastAPI()
    
    @app.get("/")
    def read_root():
        return {"error": f"Failed to import main application: {str(e)}"}

# Export the app for Vercel serverless
handler = app 