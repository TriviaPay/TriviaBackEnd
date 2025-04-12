import os
import re
from sqlalchemy import create_engine, exc
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from contextlib import contextmanager
import logging

# Load environment variables
load_dotenv()

# Database connection string
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

# If using Heroku/Vercel, convert the postgres:// URL to postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Modify URL to use pg8000 instead of psycopg2
if "postgresql" in DATABASE_URL and "driver=" not in DATABASE_URL:
    # Extract all parts of the connection URL
    pattern = r'postgresql://([^:]+):([^@]+)@([^:/]+):?(\d*)/?([^?]*)'
    match = re.match(pattern, DATABASE_URL)
    
    if match:
        username, password, host, port, dbname = match.groups()
        if not port:
            port = "5432"  # Default PostgreSQL port
        
        # Construct a new URL with the pg8000 driver
        # For pg8000 1.30.1, using connect_args instead of URL parameters
        DATABASE_URL = f"postgresql+pg8000://{username}:{password}@{host}:{port}/{dbname}"
        logging.info(f"Using pg8000 driver with URL: {DATABASE_URL.replace(password, '****')}")

# Create SQLAlchemy engine with connection pooling
engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,  # Recycle connections after 30 minutes
    pool_pre_ping=True,  # Enable connection health checks
    # For pg8000 1.30.1, use the correct connect_args format
    connect_args={
        "ssl_context": True  # This is the correct parameter for pg8000 1.30.1
    }
)

# Create session maker for database interactions
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for ORM models
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    except exc.SQLAlchemyError as e:
        db.rollback()
        raise exc.SQLAlchemyError(f"Database error: {str(e)}")
    finally:
        db.close()


def verify_db_connection():
    """
    Verify database connection is working.
    Returns True if connection is successful, False otherwise.
    """
    try:
        with engine.connect() as connection:
            connection.execute("SELECT 1")
        return True
    except Exception as e:
        logging.error(f"Database connection error: {str(e)}")
        return False
