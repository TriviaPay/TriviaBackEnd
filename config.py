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
DESCOPE_JWT_LEEWAY = int(os.getenv("DESCOPE_JWT_LEEWAY", "60"))  # default 60 seconds for JWT clock-skew tolerance
DESCOPE_JWT_LEEWAY_FALLBACK = int(os.getenv("DESCOPE_JWT_LEEWAY_FALLBACK", "120"))  # fallback 120 seconds for severe clock-skew

# Log JWT leeway configuration on startup
import logging
logging.info(f"Descope JWT leeway configured: primary={DESCOPE_JWT_LEEWAY}s, fallback={DESCOPE_JWT_LEEWAY_FALLBACK}s")

# Password storage configuration
STORE_PASSWORD_IN_DESCOPE = os.getenv("STORE_PASSWORD_IN_DESCOPE", "true").lower() == "true"
STORE_PASSWORD_IN_NEONDB = os.getenv("STORE_PASSWORD_IN_NEONDB", "true").lower() == "true"

# Live Chat settings
LIVE_CHAT_ENABLED = os.getenv("LIVE_CHAT_ENABLED", "true").lower() == "true"
LIVE_CHAT_PRE_DRAW_HOURS = int(os.getenv("LIVE_CHAT_PRE_DRAW_HOURS", "1"))  # Hours before draw
LIVE_CHAT_POST_DRAW_HOURS = int(os.getenv("LIVE_CHAT_POST_DRAW_HOURS", "1"))  # Hours after draw
LIVE_CHAT_MAX_MESSAGES_PER_USER_PER_MINUTE = int(os.getenv("LIVE_CHAT_MAX_MESSAGES_PER_USER_PER_MINUTE", "10"))
LIVE_CHAT_MESSAGE_HISTORY_LIMIT = int(os.getenv("LIVE_CHAT_MESSAGE_HISTORY_LIMIT", "100"))  # fallback 2 hours
LIVE_CHAT_MAX_MESSAGE_LENGTH = int(os.getenv("LIVE_CHAT_MAX_MESSAGE_LENGTH", "1000"))  # Maximum message length in characters

# Redis settings
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# AWS Default Profile Picture Settings
AWS_DEFAULT_PROFILE_PIC_BASE_URL = os.getenv("AWS_DEFAULT_PROFILE_PIC_BASE_URL", "")

# AWS Profile Picture Upload Settings
AWS_PROFILE_PIC_BUCKET = os.getenv("AWS_PROFILE_PIC_BUCKET", "triviapics")  # S3 bucket for custom profile pictures

# SSE settings
SSE_HEARTBEAT_SECONDS = int(os.getenv("SSE_HEARTBEAT_SECONDS", "25"))

# E2EE DM Settings
E2EE_DM_ENABLED = os.getenv("E2EE_DM_ENABLED", "false").lower() == "true"
E2EE_DM_MAX_MESSAGE_SIZE = int(os.getenv("E2EE_DM_MAX_MESSAGE_SIZE", "65536"))  # 64KB
E2EE_DM_MAX_MESSAGES_PER_MINUTE = int(os.getenv("E2EE_DM_MAX_MESSAGES_PER_MINUTE", "20"))
E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST = int(os.getenv("E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST", "5"))
E2EE_DM_BURST_WINDOW_SECONDS = int(os.getenv("E2EE_DM_BURST_WINDOW_SECONDS", "5"))
E2EE_DM_PREKEY_POOL_SIZE = int(os.getenv("E2EE_DM_PREKEY_POOL_SIZE", "100"))
E2EE_DM_OTPK_LOW_WATERMARK = int(os.getenv("E2EE_DM_OTPK_LOW_WATERMARK", "5"))
E2EE_DM_OTPK_CRITICAL_WATERMARK = int(os.getenv("E2EE_DM_OTPK_CRITICAL_WATERMARK", "2"))
E2EE_DM_SIGNED_PREKEY_ROTATION_DAYS = int(os.getenv("E2EE_DM_SIGNED_PREKEY_ROTATION_DAYS", "60"))
E2EE_DM_SIGNED_PREKEY_MAX_AGE_DAYS = int(os.getenv("E2EE_DM_SIGNED_PREKEY_MAX_AGE_DAYS", "90"))
E2EE_DM_IDENTITY_CHANGE_ALERT_THRESHOLD = int(os.getenv("E2EE_DM_IDENTITY_CHANGE_ALERT_THRESHOLD", "2"))
E2EE_DM_IDENTITY_CHANGE_BLOCK_THRESHOLD = int(os.getenv("E2EE_DM_IDENTITY_CHANGE_BLOCK_THRESHOLD", "5"))
E2EE_DM_MAX_CONCURRENT_STREAMS_PER_USER = int(os.getenv("E2EE_DM_MAX_CONCURRENT_STREAMS_PER_USER", "3"))
E2EE_DM_MESSAGE_HISTORY_LIMIT = int(os.getenv("E2EE_DM_MESSAGE_HISTORY_LIMIT", "100"))
REDIS_RETRY_INTERVAL_SECONDS = int(os.getenv("REDIS_RETRY_INTERVAL_SECONDS", "60"))
REDIS_PUBSUB_LAG_ALERT_THRESHOLD_MS = int(os.getenv("REDIS_PUBSUB_LAG_ALERT_THRESHOLD_MS", "2000"))
SSE_MAX_MISSED_HEARTBEATS = int(os.getenv("SSE_MAX_MISSED_HEARTBEATS", "2"))
