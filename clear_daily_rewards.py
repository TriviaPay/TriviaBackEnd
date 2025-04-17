from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from db import get_db
from models import UserDailyRewards
import sys

# Use the FastAPI dependency to get the database session
def clear_daily_rewards():
    """
    Utility script to clear all UserDailyRewards records.
    Useful for testing or resetting the rewards state.
    """
    print("Daily Rewards Reset Utility")
    print("===========================")
    
    try:
        # Get a database session using the same method as the API
        db = next(get_db())
        
        # Get count before deletion
        count_before = db.query(UserDailyRewards).count()
        print(f"Found {count_before} records in UserDailyRewards table")
        
        if count_before == 0:
            print("No records found. Nothing to delete.")
            return
            
        # Ask for confirmation
        if len(sys.argv) < 2 or sys.argv[1] != "--force":
            confirm = input(f"Are you sure you want to delete all {count_before} records? (y/n): ")
            if confirm.lower() != 'y':
                print("Operation cancelled.")
                return
        
        # Delete all records
        db.query(UserDailyRewards).delete()
        db.commit()
        
        # Confirm deletion
        count_after = db.query(UserDailyRewards).count()
        print(f"Successfully deleted all records. {count_after} records remaining.")
    except Exception as e:
        print(f"ERROR: {e}")
        print("Failed to delete records. Please check the database connection.")
        return 1
    finally:
        if 'db' in locals():
            db.close()
    
    return 0

if __name__ == "__main__":
    sys.exit(clear_daily_rewards()) 