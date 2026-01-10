import os
from datetime import datetime, timedelta

import pytz


def get_next_draw_time():
    """
    Calculates the next draw time based on the current time.
    Returns the next occurrence of the daily draw time (default 8 PM EST).
    """
    # Get draw time settings from environment
    draw_hour = int(os.environ.get("DRAW_TIME_HOUR", "20"))  # Default 8 PM
    draw_minute = int(os.environ.get("DRAW_TIME_MINUTE", "0"))  # Default 0 minutes
    timezone_str = os.environ.get("DRAW_TIMEZONE", "US/Eastern")  # Default EST

    # Get current time in configured timezone
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)

    # Create today's draw time
    draw_time = now.replace(hour=draw_hour, minute=draw_minute, second=0, microsecond=0)

    # If we've passed today's draw time, move to tomorrow
    if now >= draw_time:
        draw_time = draw_time + timedelta(days=1)

    return draw_time
