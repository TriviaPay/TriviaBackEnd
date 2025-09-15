import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from models import Base, User, Letter
from db import get_db
import os

# Test database URL
TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+pg8000://postgres:postgres@localhost:5432/test_db?sslmode=disable"
)

@pytest.fixture(scope="session")
def test_engine():
    """Create test database engine"""
    engine = create_engine(TEST_DATABASE_URL)
    return engine

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
        # Seed letter data
        letters = [
            Letter(letter='a', image_url='https://example.com/a.png'),
            Letter(letter='b', image_url='https://example.com/b.png'),
            Letter(letter='c', image_url='https://example.com/c.png'),
            Letter(letter='d', image_url='https://example.com/d.png'),
            Letter(letter='e', image_url='https://example.com/e.png'),
            Letter(letter='f', image_url='https://example.com/f.png'),
            Letter(letter='g', image_url='https://example.com/g.png'),
            Letter(letter='h', image_url='https://example.com/h.png'),
            Letter(letter='i', image_url='https://example.com/i.png'),
            Letter(letter='j', image_url='https://example.com/j.png'),
            Letter(letter='k', image_url='https://example.com/k.png'),
            Letter(letter='l', image_url='https://example.com/l.png'),
            Letter(letter='m', image_url='https://example.com/m.png'),
            Letter(letter='n', image_url='https://example.com/n.png'),
            Letter(letter='o', image_url='https://example.com/o.png'),
            Letter(letter='p', image_url='https://example.com/p.png'),
            Letter(letter='q', image_url='https://example.com/q.png'),
            Letter(letter='r', image_url='https://example.com/r.png'),
            Letter(letter='s', image_url='https://example.com/s.png'),
            Letter(letter='t', image_url='https://example.com/t.png'),
            Letter(letter='u', image_url='https://example.com/u.png'),
            Letter(letter='v', image_url='https://example.com/v.png'),
            Letter(letter='w', image_url='https://example.com/w.png'),
            Letter(letter='x', image_url='https://example.com/x.png'),
            Letter(letter='y', image_url='https://example.com/y.png'),
            Letter(letter='z', image_url='https://example.com/z.png'),
        ]
        db.add_all(letters)
        
        # Add some test users
        test_users = [
            User(
                descope_user_id="test_user_1",
                email="test1@example.com",
                username="testuser1",
                display_name="Test User 1"
            ),
            User(
                descope_user_id="test_user_2",
                email="test2@example.com",
                username="testuser2",
                display_name="Test User 2"
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