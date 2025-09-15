import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Application environment
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
DEBUG = ENVIRONMENT == "development"

# Database settings
DATABASE_URL = os.getenv("DATABASE_URL")

# Stripe settings
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Application settings
APP_NAME = "TriviaPay API"
APP_VERSION = "1.0.0"

# Descope settings
DESCOPE_PROJECT_ID = os.getenv("DESCOPE_PROJECT_ID", "")
DESCOPE_MANAGEMENT_KEY = os.getenv("DESCOPE_MANAGEMENT_KEY", "")
DESCOPE_JWT_LEEWAY = int(os.getenv("DESCOPE_JWT_LEEWAY", "3600"))  # default 1 hour for local dev time sync issues
DESCOPE_JWT_LEEWAY_FALLBACK = int(os.getenv("DESCOPE_JWT_LEEWAY_FALLBACK", "7200"))  # fallback 2 hours
