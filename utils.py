from datetime import datetime, timedelta
import pytz
from db import get_db
from models import Letter
import logging

def get_next_draw_time():
    """
    Calculates the next draw time: the last day of the current month at 8 PM EST.
    """
    # Get current time in EST
    est = pytz.timezone("US/Eastern")
    now = datetime.now(est)

    # Calculate the first day of the next month
    first_of_next_month = (now.replace(day=1) + timedelta(days=32)).replace(day=1)

    # Calculate the last day of the current month
    last_day_of_this_month = first_of_next_month - timedelta(days=1)

    # Set draw time to 8 PM EST on the last day of the current month
    draw_time = last_day_of_this_month.replace(hour=20, minute=0, second=0, microsecond=0)

    return draw_time

def get_letter_profile_pic(username: str, db=None) -> str:
    """
    Get profile picture URL based on first letter of username.
    
    Args:
        username (str): The username to get a profile picture for
        db (Session, optional): Database session. If not provided, a new session will be created.
        
    Returns:
        str: URL of profile picture for the first letter of the username
    """
    if not username:
        return None
    
    # Get the first letter of the username, convert to lowercase
    first_letter = username[0].lower()
    
    # If not a letter, default to 'a'
    if not first_letter.isalpha():
        first_letter = 'a'
    
    # Use provided DB session or create new one
    close_db = False
    if db is None:
        close_db = True
        db = next(get_db())
    
    try:
        # Query the letters table for the image URL
        letter_row = db.query(Letter).filter(Letter.letter == first_letter).first()
        
        if letter_row and letter_row.image_url:
            return letter_row.image_url
        else:
            # Log warning and use default letter 'a' as fallback
            logging.warning(f"No image URL found for letter '{first_letter}', using default")
            default_letter = db.query(Letter).filter(Letter.letter == 'a').first()
            return default_letter.image_url if default_letter else None
    
    except Exception as e:
        logging.error(f"Error getting letter profile pic: {str(e)}")
        return None
    
    finally:
        # Close DB session if we created it
        if close_db and db:
            db.close()
