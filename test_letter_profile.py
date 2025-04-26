from db import get_db
from models import User, Letter
from utils import get_letter_profile_pic
import logging

logging.basicConfig(level=logging.INFO)

def test_letter_profile_pic():
    """Test the letter-based profile picture functionality"""
    db = next(get_db())
    
    try:
        # Test different usernames
        test_usernames = [
            "alex", "Benjamin", "charlie", "Diana", "123user", 
            "", None, "Zack", "yolanda", "_special"
        ]
        
        print("\nTesting letter-based profile pictures:")
        print("======================================")
        
        for username in test_usernames:
            profile_pic = get_letter_profile_pic(username, db)
            if username:
                first_letter = username[0].lower() if username[0].isalpha() else "a"
                print(f"Username: {username} -> First letter: {first_letter} -> Profile pic: {profile_pic}")
            else:
                print(f"Username: {username} -> Profile pic: {profile_pic}")
        
        # Get a sample of actual users and check their profile pics
        users = db.query(User).limit(5).all()
        
        print("\nExisting Users:")
        print("==============")
        
        for user in users:
            print(f"User ID: {user.account_id}, Username: {user.username}, Current Profile Pic: {user.profile_pic_url}")
            
            # Get what the new profile pic should be
            new_pic = get_letter_profile_pic(user.username, db)
            print(f"  -> New letter-based profile pic would be: {new_pic}")
            print()
            
    finally:
        db.close()

if __name__ == "__main__":
    test_letter_profile_pic() 