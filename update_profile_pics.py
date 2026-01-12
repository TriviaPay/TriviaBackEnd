import logging

from core.db import get_db
from models import User
from utils import get_letter_profile_pic

# Configure logging to output to console
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def update_all_profile_pictures():
    """
    Update all users' profile pictures to be based on the first letter of their username.
    """
    db = next(get_db())

    try:
        # Get all users
        print("Fetching all users from database...")
        users = db.query(User).all()
        print(f"Found {len(users)} users to update")

        updated_count = 0
        skipped_count = 0

        # Update each user's profile picture
        for i, user in enumerate(users):
            if i % 10 == 0 and i > 0:
                print(f"Processed {i}/{len(users)} users...")

            if not user.username:
                print(f"User ID {user.account_id} has no username, skipping")
                skipped_count += 1
                continue

            # Get the new profile picture URL
            new_profile_pic = get_letter_profile_pic(user.username, db)

            # For verbose output, show what we're doing
            if i < 5 or i % 50 == 0:  # Only show first 5 and then every 50th
                print(
                    f"User {user.account_id} ({user.username}): {user.profile_pic_url} -> {new_profile_pic}"
                )

            # Update the user's profile picture
            user.profile_pic_url = new_profile_pic
            updated_count += 1

        # Commit all changes
        db.commit()
        print(
            f"Successfully updated {updated_count} profile pictures, skipped {skipped_count} users"
        )

    except Exception as e:
        db.rollback()
        print(f"Error updating profile pictures: {str(e)}")
    finally:
        db.close()


if __name__ == "__main__":
    print("Starting profile picture update process...")
    update_all_profile_pictures()
    print("Profile picture update process completed")
