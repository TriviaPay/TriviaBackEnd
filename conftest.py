import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from models import Base, User
from db import get_db
import os
import urllib.parse
import ssl

def create_test_engine(database_url):
    """Create test database engine with proper SSL handling for different drivers"""
    # Parse the URL to extract any query parameters
    parsed = urllib.parse.urlparse(database_url)
    query_params = urllib.parse.parse_qs(parsed.query)
    
    # Get SSL mode from URL parameters
    ssl_mode = query_params.get('sslmode', [None])[0]
    
    # Determine if we're using pg8000 or psycopg2
    is_pg8000 = 'pg8000' in database_url
    
    # For pg8000, we need to remove sslmode from URL and handle it in connect_args
    if is_pg8000:
        # Remove sslmode from query parameters since pg8000 doesn't support it in URL
        if 'sslmode' in query_params:
            del query_params['sslmode']
        
        # Reconstruct URL without sslmode parameter
        new_query = urllib.parse.urlencode(query_params, doseq=True)
        clean_url = urllib.parse.urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            parsed.params, new_query, parsed.fragment
        ))
        
        # Set up connect_args based on SSL mode for pg8000
        connect_args = {}
        if ssl_mode == 'require' or (ssl_mode is None and not os.getenv("TESTING", "false").lower() == "true"):
            # Use SSL for production (default) or when explicitly required
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            connect_args["ssl_context"] = ssl_context
        
        return create_engine(
            clean_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            echo=False,
            connect_args=connect_args
        )
    else:
        # For psycopg2, we can keep sslmode in the URL as it supports it natively
        # But we need to ensure we're using postgresql+psycopg2 explicitly
        if database_url.startswith('postgresql://') and '+' not in database_url:
            database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://', 1)
        
        return create_engine(
            database_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            echo=False
        )

# Test database URL
TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+pg8000://postgres:postgres@localhost:5432/test_db?sslmode=disable"
)

@pytest.fixture(scope="session")
def test_engine():
    """Create test database engine"""
    return create_test_engine(TEST_DATABASE_URL)

@pytest.fixture(scope="function")
def test_db(test_engine):
    """Create all tables before each test and drop them after"""
    # Drop all tables with CASCADE
    with test_engine.connect() as connection:
        # Get all table names
        tables = connection.execute(text("""
            SELECT tablename FROM pg_tables 
            WHERE schemaname = 'public'
        """)).fetchall()
        
        # Drop each table with CASCADE
        for table in tables:
            connection.execute(text(f'DROP TABLE IF EXISTS "{table[0]}" CASCADE'))
        connection.commit()
    
    # Create all tables
    Base.metadata.create_all(bind=test_engine)
    
    # Create test session
    TestingSessionLocal = sessionmaker(bind=test_engine)
    db = TestingSessionLocal()
    
    try:
        # Letters table removed - no longer seeding letter data
        
        # Add some test users
        test_users = [
            User(
                descope_user_id="test_user_1",
                email="test1@example.com",
                username="testuser1"
            ),
            User(
                descope_user_id="test_user_2",
                email="test2@example.com",
                username="testuser2"
            )
        ]
        db.add_all(test_users)
        
        # Commit the test data
        db.commit()
        
        yield db
    finally:
        db.close()
        # Drop all tables with CASCADE after test
        with test_engine.connect() as connection:
            tables = connection.execute(text("""
                SELECT tablename FROM pg_tables 
                WHERE schemaname = 'public'
            """)).fetchall()
            for table in tables:
                connection.execute(text(f'DROP TABLE IF EXISTS "{table[0]}" CASCADE'))
            connection.commit()

@pytest.fixture
def db():
    """Override the regular get_db dependency with our test database"""
    return next(get_db()) 
