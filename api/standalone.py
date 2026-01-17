import os
import sys

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

from main import app as handler

app = handler
