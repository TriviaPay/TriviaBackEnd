import datetime
import os
import json
from dotenv import load_dotenv
import random
import jose.jwt  # Use python-jose for consistent JWT encoding

# Load environment variables
load_dotenv()

def generate_test_token(email):
    """
    Generate a test JWT token for authentication testing
    
    Args:
        email (str): Email to use in the token
    
    Returns:
        str: JWT token
    """
    # Get configuration from environment
    AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "")
    API_AUDIENCE = os.getenv("API_AUDIENCE", "")
    AUTH0_CLIENT_SECRET = os.getenv("AUTH0_CLIENT_SECRET", "")
    
    # Current time
    now = datetime.datetime.utcnow()
    
    # Token payload
    payload = {
        # Standard JWT claims
        "iss": f"https://{AUTH0_DOMAIN}/",  # Issuer
        "sub": f"email|{email.replace('@', '_')}",  # Subject
        "aud": [API_AUDIENCE, f"https://{AUTH0_DOMAIN}/userinfo"],  # Audience
        "iat": int(now.timestamp()),  # Issued at (as Unix timestamp)
        "exp": int((now + datetime.timedelta(hours=1)).timestamp()),  # Expiration time
        
        # Custom claims
        "email": email,
        "email_verified": True,
        "name": "Test User",
        
        # Additional Auth0-like claims
        "azp": "test_client_id",  # Authorized party
        "scope": "openid profile email"
    }
    
    # Encode the token using python-jose
    token = jose.jwt.encode(
        payload, 
        AUTH0_CLIENT_SECRET, 
        algorithm="HS256",
        headers={
            "typ": "JWT",
            "alg": "HS256",
            "kid": "local_test_key"  # Add a consistent key ID
        }
    )
    
    return token

def generate_test_refresh_token(email):
    """
    Generate a test refresh token
    
    Args:
        email (str): Email to use in the token
    
    Returns:
        str: Refresh token
    """
    # Get configuration from environment
    AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "")
    API_AUDIENCE = os.getenv("API_AUDIENCE", "")
    AUTH0_CLIENT_SECRET = os.getenv("AUTH0_CLIENT_SECRET", "")
    
    # Current time
    now = datetime.datetime.utcnow()
    
    # Refresh token payload
    payload = {
        "iss": f"https://{AUTH0_DOMAIN}/",
        "sub": f"email|{email.replace('@', '_')}",
        "aud": API_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(days=30)).timestamp()),
    }
    
    # Encode the refresh token
    refresh_token = jose.jwt.encode(
        payload, 
        AUTH0_CLIENT_SECRET, 
        algorithm="HS256",
        headers={
            "typ": "JWT",
            "alg": "HS256",
            "kid": "local_test_refresh_key"
        }
    )
    
    return refresh_token

def generate_unique_account_id(db):
    """
    Generate a unique 10-digit account ID
    
    Args:
        db (Session): Database session
    
    Returns:
        int: Unique account ID
    """
    from models import User
    
    while True:
        # Generate a 10-digit random number
        account_id = int("".join(str(random.randint(0, 9)) for _ in range(10)))
        
        # Check if this account_id already exists
        existing_user = db.query(User).filter(User.account_id == account_id).first()
        
        # If no user found with this ID, return it
        if not existing_user:
            return account_id

def create_test_user(email):
    """
    Create a test user in the database
    
    Args:
        email (str): Email of the test user
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from models import Base, User
    from config import DATABASE_URL

    # Create engine and session
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        # Check if user already exists by email or sub
        sub = f"auth0|test_user_{email.replace('@', '_')}"
        existing_user = db.query(User).filter(
            (User.email == email) | (User.sub == sub)
        ).first()
        
        if existing_user:
            print(f"User with email {email} or sub {sub} already exists.")
            
            # Update the existing user if needed
            if not existing_user.sub:
                existing_user.sub = sub
            
            db.commit()
            return existing_user.account_id

        # Generate a unique account ID
        unique_account_id = generate_unique_account_id(db)

        # Create a new user with the unique account ID
        new_user = User(
            account_id=unique_account_id,
            email=email,
            first_name="Test",
            last_name="User",
            sub=sub  # Add sub attribute
        )
        db.add(new_user)
        db.commit()
        print(f"Created test user with email: {email}, account_id: {unique_account_id}")
        return unique_account_id
    except Exception as e:
        db.rollback()
        print(f"Error creating user: {e}")
        return None
    finally:
        db.close()

def main():
    # Test email
    test_email = "priyatrivia1@gmail.com"
    
    # Create test user
    account_id = create_test_user(test_email)
    
    if account_id is None:
        print("Failed to create or find user.")
        return
    
    # Generate test token and refresh token
    test_token = generate_test_token(test_email)
    test_refresh_token = generate_test_refresh_token(test_email)
    
    print("\nTest User Details:")
    print(f"Email: {test_email}")
    print(f"Account ID: {account_id}")
    
    print("\nTest Token:")
    print(test_token)
    print("\nTest Refresh Token:")
    print(test_refresh_token)
    print("\nInstructions:")
    print("1. Copy the test token")
    print("2. In Swagger UI, click 'Authorize'")
    print("3. Enter: Bearer " + test_token)

if __name__ == "__main__":
    main()