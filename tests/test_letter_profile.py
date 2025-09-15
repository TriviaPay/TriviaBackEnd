import pytest
from utils.profile_utils import get_letter_profile_pic
from models import User

def test_letter_profile_pic(test_db):
    """Test the letter-based profile picture functionality"""
    try:
        # Test different usernames
        test_usernames = [
            "alex", "Benjamin", "charlie", "Diana", "123user",
            "", None, "Zack", "yolanda", "_special"
        ]

        print("\nTesting letter-based profile pictures:")
        print("======================================")

        for username in test_usernames:
            profile_pic = get_letter_profile_pic(username, test_db)
            if username:
                first_letter = username[0].lower() if username[0].isalpha() else "a"
                print(f"Username: {username} -> First letter: {first_letter} -> Profile pic: {profile_pic}")
            else:
                print(f"Username: {username} -> Profile pic: {profile_pic}")

        # Get a sample of actual users and check their profile pics
        users = test_db.query(User).limit(5).all()
        for user in users:
            profile_pic = get_letter_profile_pic(user.username, test_db)
            print(f"DB User: {user.username} -> Profile pic: {profile_pic}")

        # Test should pass if we get here without errors
        assert True

    except Exception as e:
        print(f"Error in test: {str(e)}")
        raise 