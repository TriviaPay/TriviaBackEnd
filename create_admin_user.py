import datetime
import time

import jwt

from db import get_db
from models import User


def create_admin_user():
    # Get database session
    db = next(get_db())

    try:
        # Check if user exists
        existing_user = db.query(User).filter(User.email == "admin@example.com").first()

        if existing_user:
            # Delete existing user
            print(f"Deleting existing user with email: admin@example.com")
            db.delete(existing_user)
            db.commit()

        # Create admin user
        admin_user = User(
            account_id=1234567890,
            email="admin@example.com",
            sub="email|admin",  # Use the same sub value as in our test token
            is_admin=True,
            profile_pic_url="https://example.com/admin.png",
            sign_up_date=datetime.datetime.utcnow(),
            username="admin",
            wallet_balance=1000.0,
            last_wallet_update=datetime.datetime.utcnow(),
        )

        db.add(admin_user)
        db.commit()
        db.refresh(admin_user)

        print(f"Admin user created successfully:")
        print(f"  Account ID: {admin_user.account_id}")
        print(f"  Email: {admin_user.email}")
        print(f"  Username: {admin_user.username}")
        print(f"  Sub: {admin_user.sub}")
        print(f"  Is Admin: {admin_user.is_admin}")
        print(f"  Wallet Balance: ${admin_user.wallet_balance}")

        # Create test token
        client_secret = (
            "X21fEAyjVll71k8wqVYiXnuj2rxc-7U2DFeYGm5E-m9iFwEM1hcwIW8VKfXf3AA8"
        )
        payload = {
            "sub": "email|admin",
            "email": "admin@example.com",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "iss": "https://triviapay.us.auth0.com/",
            "aud": [
                "https://triviapay.us.auth0.com/api/v2/",
                "https://triviapay.us.auth0.com/userinfo",
            ],
        }
        token = jwt.encode(payload, client_secret, algorithm="HS256")

        print("\nTest token for API calls:")
        print(f"{token}")

        return admin_user
    except Exception as e:
        db.rollback()
        print(f"Error creating admin user: {e}")
        return None
    finally:
        db.close()


if __name__ == "__main__":
    create_admin_user()
