import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Application environment
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
DEBUG = ENVIRONMENT == "development"

# Database settings
DATABASE_URL = os.getenv("DATABASE_URL")

# Auth0 settings
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN")
AUTH0_API_AUDIENCE = os.getenv("AUTH0_API_AUDIENCE")
AUTH0_ISSUER = os.getenv("AUTH0_ISSUER")
AUTH0_ALGORITHMS = ["RS256"]
AUTH0_CLIENT_ID = os.getenv("AUTH0_CLIENT_ID", "")
AUTH0_CLIENT_SECRET = os.getenv("AUTH0_CLIENT_SECRET", "")
API_AUDIENCE = os.getenv("API_AUDIENCE", "")

# JWT settings
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "secret_key_for_development_only")
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 60

# Stripe settings
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Application settings
APP_NAME = "TriviaPay API"
APP_VERSION = "1.0.0"
