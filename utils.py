from datetime import datetime, timedelta

import pytz


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
    draw_time = last_day_of_this_month.replace(
        hour=20, minute=0, second=0, microsecond=0
    )

    return draw_time


def get_letter_profile_pic(username: str, db=None) -> str:
    """
    Get profile picture URL based on first letter of username.

    Note: Letters table has been removed. This function now returns None.
    Use custom profile pictures or avatars instead.

    Args:
        username (str): The username to get a profile picture for
        db (Session, optional): Database session (no longer used, kept for
            compatibility)

    Returns:
        str: None (letters table removed)
    """
    # Letters table has been removed - return None
    # Profile pictures should use custom uploads or avatars instead
    return None
