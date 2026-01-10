#!/usr/bin/env python3
"""
Clean up unused daily questions and refresh daily question pool.
New logic:
1. Delete rows from trivia_questions_daily where date=previous_draw_date AND is_used=false
2. Reset those questions in trivia table
3. Check next draw's pool count, add questions if < 4

Timing:
- Draw happens at 6:00 PM EST
- Question reset happens at 6:01 PM EST
- After 6:01 PM on Day 1 until 5:59 PM on Day 2, users work on the "next draw" (Day 2's draw)
- So when reset runs at 6:01 PM, it populates the next draw's pool
"""

import logging
import os
import sys
from datetime import date, datetime, timedelta

import pytz
from sqlalchemy import func
from sqlalchemy.orm import Session

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import SessionLocal

# Legacy tables removed: TriviaQuestionsDaily, Trivia
# This script is deprecated - use mode-specific question management instead

# Use existing logger configuration from main app (don't call basicConfig)
logger = logging.getLogger(__name__)


def get_today_in_app_timezone() -> date:
    """Get today's date in the app's timezone (EST/US Eastern)."""
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    return now.date()


def get_date_range_for_query(target_date: date):
    """
    Get start and end datetime for a date in the app timezone.
    Returns tuple of (start_datetime, end_datetime) in UTC for database comparison.
    """
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)

    # Create start and end of day in app timezone
    start_of_day = tz.localize(datetime.combine(target_date, datetime.min.time()))
    end_of_day = tz.localize(datetime.combine(target_date, datetime.max.time()))

    # Convert to UTC for database comparison (most databases store in UTC)
    start_utc = start_of_day.astimezone(pytz.UTC).replace(tzinfo=None)
    end_utc = end_of_day.astimezone(pytz.UTC).replace(tzinfo=None)

    return start_utc, end_utc


# Legacy cleanup_unused_questions function removed - TriviaQuestionsDaily and Trivia tables deleted
# Use mode-specific question management instead (allocate_free_mode_questions, allocate_bronze_mode_questions, etc.)
def cleanup_unused_questions():
    """
    DEPRECATED: Legacy function removed - TriviaQuestionsDaily and Trivia tables deleted.
    Use mode-specific question allocation functions instead.
    """
    logger.warning(
        "cleanup_unused_questions() is deprecated - legacy tables removed. Use mode-specific question allocation instead."
    )
    pass


if __name__ == "__main__":
    # Configure logging when run directly
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    cleanup_unused_questions()
