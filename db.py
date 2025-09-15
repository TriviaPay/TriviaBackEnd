import os
import re
import ssl
from sqlalchemy import create_engine, MetaData
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager
from dotenv import load_dotenv
import urllib.parse

# Load environment variables
load_dotenv()

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
    ssl_mode = query_params.get('sslmode', [None])[0]
    
    # Modify URL to use pg8000 instead of psycopg2
    if "postgresql" in DATABASE_URL and "driver=" not in DATABASE_URL:
        # Extract all parts of the connection URL
        pattern = r'postgresql://([^:]+):([^@]+)@([^:/]+):?(\d*)/?([^?]*)'
        match = re.match(pattern, DATABASE_URL)
        
        if match:
            username, password, host, port, dbname = match.groups()
            
            # Default port if not specified
            if not port:
                port = "5432"
                
            # Reconstruct URL with pg8000 driver (without URL-level SSL params)
            DATABASE_URL = f"postgresql+pg8000://{username}:{password}@{host}:{port}/{dbname}"

# Create engine with appropriate settings
if DATABASE_URL.startswith("sqlite"):
    # SQLite settings for testing
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False}
    )
else:
    # PostgreSQL settings with SSL support for pg8000
    connect_args = {}
    
    # Handle SSL configuration based on environment and URL parameters
    if ssl_mode == 'disable' or TESTING:
        # No SSL for testing or when explicitly disabled
        pass
    elif ssl_mode == 'require' or not ssl_mode:
        # Use SSL for production (default) or when explicitly required
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        connect_args["ssl_context"] = ssl_context
    
    engine = create_engine(
        DATABASE_URL,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        echo=False,
        pool_recycle=300,
        connect_args=connect_args
    )

# Create SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create Base class
Base = declarative_base()

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
