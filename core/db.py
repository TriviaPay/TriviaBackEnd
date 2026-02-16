import io
import logging
import os
import re
import ssl
import sys
import time
import urllib.parse
import warnings
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

# Suppress ALL warnings from dotenv BEFORE importing it
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*dotenv.*")
warnings.filterwarnings("ignore", message=".*Python-dotenv.*")


# Create a filter for stderr that removes dotenv warnings
class FilteredStderr:
    def __init__(self, original_stderr):
        self.original_stderr = original_stderr

    def write(self, text):
        # Filter out dotenv-related warnings
        if text and ("dotenv" not in text.lower() and "Python-dotenv" not in text):
            self.original_stderr.write(text)

    def flush(self):
        self.original_stderr.flush()

    def __getattr__(self, name):
        return getattr(self.original_stderr, name)


# Redirect stderr to filter out dotenv warnings (only if not already filtered)
if not isinstance(sys.stderr, FilteredStderr):
    _filtered_stderr = FilteredStderr(sys.stderr)
    sys.stderr = _filtered_stderr

from dotenv import load_dotenv

# Load environment variables
load_dotenv(override=False)

# Database connection string
DATABASE_URL = os.getenv("DATABASE_URL")

# Check if we're in a testing environment
TESTING = os.getenv("TESTING", "false").lower() == "true"

if not DATABASE_URL:
    if TESTING:
        # In testing environment, use SQLite in-memory database as fallback
        DATABASE_URL = "sqlite:///:memory:"
        print("⚠️  Using in-memory SQLite database for testing")
    else:
        raise ValueError("DATABASE_URL environment variable is not set")

# Only apply PostgreSQL-specific modifications if we're actually using PostgreSQL
if DATABASE_URL and not DATABASE_URL.startswith("sqlite"):
    # If using Heroku/Vercel, convert the postgres:// URL to postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    # Parse the URL to extract any query parameters
    parsed = urllib.parse.urlparse(DATABASE_URL)
    query_params = urllib.parse.parse_qs(parsed.query)

    # Get SSL mode from URL or default based on environment
    ssl_mode = query_params.get("sslmode", [None])[0]

    # Modify URL to use pg8000 instead of psycopg2
    if "postgresql" in DATABASE_URL and "driver=" not in DATABASE_URL:
        # Extract all parts of the connection URL
        pattern = r"postgresql://([^:]+):([^@]+)@([^:/]+):?(\d*)/?([^?]*)"
        match = re.match(pattern, DATABASE_URL)

        if match:
            username, password, host, port, dbname = match.groups()

            # Default port if not specified
            if not port:
                port = "5432"

            # Reconstruct URL with pg8000 driver (without URL-level SSL params)
            DATABASE_URL = (
                f"postgresql+pg8000://{username}:{password}@{host}:{port}/{dbname}"
            )

# Create engine with appropriate settings
if DATABASE_URL.startswith("sqlite"):
    # SQLite settings for testing
    engine = create_engine(
        DATABASE_URL, echo=False, connect_args={"check_same_thread": False}
    )
else:
    # PostgreSQL settings with SSL support for pg8000
    connect_args = {}
    pool_size = int(os.getenv("DB_POOL_SIZE", "10"))
    max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "20"))
    pool_timeout = int(os.getenv("DB_POOL_TIMEOUT_SECONDS", "30"))
    pool_recycle = int(os.getenv("DB_POOL_RECYCLE_SECONDS", "300"))

    # Handle SSL configuration based on environment and URL parameters
    if ssl_mode == "disable" or TESTING:
        # No SSL for testing or when explicitly disabled
        pass
    elif ssl_mode == "require" or not ssl_mode:
        # Use SSL for production (default) or when explicitly required
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connect_args["ssl_context"] = ssl_context

    engine = create_engine(
        DATABASE_URL,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_pre_ping=True,
        echo=False,
        pool_recycle=pool_recycle,
        connect_args=connect_args,
    )

# Create SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create Base class
Base = declarative_base()


def _install_slow_query_logging(_engine):
    threshold_ms = float(os.getenv("SLOW_DB_QUERY_THRESHOLD_MS", "200"))
    if threshold_ms <= 0:
        return

    logger = logging.getLogger("db.slow_query")

    @event.listens_for(_engine, "before_cursor_execute")
    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        context._query_start_time = time.perf_counter()

    @event.listens_for(_engine, "after_cursor_execute")
    def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        start = getattr(context, "_query_start_time", None)
        if start is None:
            return
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if elapsed_ms < threshold_ms:
            return

        stmt = " ".join(str(statement).split())
        if len(stmt) > 500:
            stmt = stmt[:500] + "…"
        params_repr = repr(parameters)
        if len(params_repr) > 500:
            params_repr = params_repr[:500] + "…"

        logger.warning(
            "SLOW_DB_QUERY | ms=%.1f | stmt=%s | params=%s",
            elapsed_ms,
            stmt,
            params_repr,
        )


_install_slow_query_logging(engine)


def get_db():
    """Dependency to get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context():
    """Context manager for database sessions"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    """Create all tables"""
    Base.metadata.create_all(bind=engine)


def drop_tables():
    """Drop all tables"""
    Base.metadata.drop_all(bind=engine)
