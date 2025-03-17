import os
from sqlalchemy import create_engine, exc
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from contextlib import contextmanager

# Load environment variables
load_dotenv()

# Database connection string
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

# Create SQLAlchemy engine with connection pooling
engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,  # Recycle connections after 30 minutes
    pool_pre_ping=True  # Enable connection health checks
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
        with get_db() as db:
            db.execute("SELECT 1")
        return True
    except Exception as e:
        print(f"Database connection error: {str(e)}")
        return False
