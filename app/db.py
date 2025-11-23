"""
Async Database Configuration
"""
import os
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from dotenv import load_dotenv

load_dotenv()

# Get DATABASE_URL from environment
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

# Parse URL to handle connection parameters
# asyncpg doesn't support many psycopg2-style query parameters
parsed = urlparse(DATABASE_URL)
query_params = parse_qs(parsed.query)

# Extract sslmode before removing all query params
# asyncpg doesn't support query string parameters, so we remove them all
sslmode = query_params.pop('sslmode', [None])[0]

# Rebuild URL without any query parameters (asyncpg doesn't support them)
new_parsed = parsed._replace(query='')
DATABASE_URL = urlunparse(new_parsed)

# Configure SSL for asyncpg (only parameter we support)
connect_args = {}
if sslmode:
    # Convert sslmode to asyncpg's SSL format
    if sslmode in ['require', 'prefer', 'allow', 'verify-ca', 'verify-full']:
        connect_args['ssl'] = True
    elif sslmode == 'disable':
        connect_args['ssl'] = False

# Convert postgresql:// to postgresql+asyncpg:// for async support
if DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://"):
    if "postgres://" in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
    elif "postgresql://" in DATABASE_URL and "+asyncpg" not in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# Create async engine with SSL configuration
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args=connect_args if connect_args else {},
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Create declarative base
Base = declarative_base()


async def get_async_db():
    """Dependency to get async database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

