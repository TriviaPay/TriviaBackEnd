#!/usr/bin/env python3
"""
Admin User Management Script

This script helps manage admin users in the database.
Since we removed the environment variable admin method, all admin access
is now controlled by the admin_users table.
"""

import os
import sys

from sqlalchemy.orm import Session

# Add the current directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import get_db
from models import AdminUser, User


def list_admin_users():
    """List all admin users"""
    db = next(get_db())
    try:
        admin_entry = db.query(AdminUser).first()

        if not admin_entry:
            print("No admin users found.")
            return
        user = db.query(User).filter(User.account_id == admin_entry.user_id).first()
        print("Current Admin User:")
        print("-" * 50)
        print(f"Email: {admin_entry.email}")
        print(f"Username: {user.username if user else 'Unknown'}")
        print(f"Account ID: {admin_entry.user_id}")
        print(f"Created: {admin_entry.created_at}")
        print("-" * 50)

    finally:
        db.close()


def make_admin(email: str):
    """Make a user an admin by email"""
    db = next(get_db())
    try:
        user = db.query(User).filter(User.email == email).first()

        if not user:
            print(f"❌ User with email '{email}' not found.")
            return False

        existing_admin = db.query(AdminUser).first()
        if existing_admin:
            if existing_admin.user_id == user.account_id:
                print(f"ℹ️  User '{email}' is already the admin.")
                return True
            db.delete(existing_admin)
            db.flush()

        db.add(AdminUser(user_id=user.account_id, email=user.email))
        db.commit()

        print(f"✅ User '{email}' is now an admin.")
        return True

    except Exception as e:
        print(f"❌ Error making user admin: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def remove_admin(email: str):
    """Remove admin privileges from a user by email"""
    db = next(get_db())
    try:
        user = db.query(User).filter(User.email == email).first()

        if not user:
            print(f"❌ User with email '{email}' not found.")
            return False

        existing_admin = db.query(AdminUser).first()
        if not existing_admin or existing_admin.user_id != user.account_id:
            print(f"ℹ️  User '{email}' is not an admin.")
            return True

        db.delete(existing_admin)
        db.commit()

        print(f"✅ Admin privileges removed from '{email}'.")
        return True

    except Exception as e:
        print(f"❌ Error removing admin privileges: {e}")
        db.rollback()
        return False
    finally:
        db.close()


def main():
    """Main function to handle command line arguments"""
    if len(sys.argv) < 2:
        print("Usage:")
        print(
            "  python manage_admin_users.py list                    # List all admin users"
        )
        print("  python manage_admin_users.py make <email>           # Make user admin")
        print(
            "  python manage_admin_users.py remove <email>         # Remove admin privileges"
        )
        return

    command = sys.argv[1].lower()

    if command == "list":
        list_admin_users()
    elif command == "make":
        if len(sys.argv) < 3:
            print("❌ Please provide an email address.")
            return
        email = sys.argv[2]
        make_admin(email)
    elif command == "remove":
        if len(sys.argv) < 3:
            print("❌ Please provide an email address.")
            return
        email = sys.argv[2]
        remove_admin(email)
    else:
        print(f"❌ Unknown command: {command}")
        print("Available commands: list, make, remove")


if __name__ == "__main__":
    main()
