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

import sys
import os
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, date, timedelta
import pytz
import logging

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import SessionLocal
from models import TriviaQuestionsDaily, Trivia

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

def cleanup_unused_questions():
    """
    Clean up unused daily questions and refresh the pool for the next draw.
    
    This should be run daily at 6:01 PM EST (after the draw at 6:00 PM).
    The logic:
    - Draw happens at 6:00 PM EST
    - Reset happens at 6:01 PM EST
    - After 6:01 PM on Day 1 until 5:59 PM on Day 2, users work on the "next draw" (Day 2's draw)
    - So we populate the next draw's pool so questions are ready for users
    
    By populating the next draw's pool, questions are ready when users access them.
    """
    logger.info("=" * 80)
    logger.info("🧹 CLEANUP_UNUSED_QUESTIONS FUNCTION CALLED")
    logger.info(f"⏰ Timestamp: {datetime.now()}")
    logger.info("=" * 80)
    
    db: Session = SessionLocal()
    
    try:
        today = get_today_in_app_timezone()
        next_draw_date = today + timedelta(days=1)  # Next draw is always tomorrow
        previous_draw_date = today - timedelta(days=1)  # Previous draw was yesterday
        
        # Step 1: Delete unused questions from previous draw - use timezone-aware date range
        start_datetime, end_datetime = get_date_range_for_query(previous_draw_date)
        previous_draw_unused = db.query(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= start_datetime,
            TriviaQuestionsDaily.date <= end_datetime,
            TriviaQuestionsDaily.is_used == False
        ).all()
        
        deleted_count = 0
        question_numbers_to_reset = []
        
        for daily_q in previous_draw_unused:
            question_numbers_to_reset.append(daily_q.question_number)
            db.delete(daily_q)
            deleted_count += 1
        
        # Step 2: Reset questions in trivia table
        if question_numbers_to_reset:
            db.query(Trivia).filter(
                Trivia.question_number.in_(question_numbers_to_reset)
            ).update({
                Trivia.question_done: False,
                Trivia.que_displayed_date: None
            }, synchronize_session=False)
            logger.info(f"Reset {len(question_numbers_to_reset)} questions in trivia table")
        
        db.commit()
        logger.info(f"Deleted {deleted_count} unused questions from previous draw ({previous_draw_date})")
        
        # Check total questions in database
        total_questions_in_db = db.query(Trivia).count()
        unused_questions_in_db = db.query(Trivia).filter(Trivia.question_done == False).count()
        used_questions_in_db = db.query(Trivia).filter(Trivia.question_done == True).count()
        logger.info(f"📊 Database stats: {total_questions_in_db} total questions, {unused_questions_in_db} unused, {used_questions_in_db} used")
        
        if total_questions_in_db == 0:
            error_msg = "CRITICAL ERROR: No questions exist in the trivia table!"
            logger.error(f"❌ {error_msg}")
            raise Exception(error_msg)
        
        # Step 3: Check next draw's pool and add questions if needed - use timezone-aware date range
        # We populate the next draw's pool so questions are ready when users access them
        # After 6:01 PM on Day 1 until 5:59 PM on Day 2, users work on Day 2's draw
        start_datetime, end_datetime = get_date_range_for_query(next_draw_date)
        next_pool_count = db.query(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= start_datetime,
            TriviaQuestionsDaily.date <= end_datetime
        ).count()
        
        logger.info(f"Next draw's pool has {next_pool_count} questions")
        logger.info(f"Today in app timezone: {today}")
        logger.info(f"Next draw date: {next_draw_date}")
        logger.info(f"Previous draw date: {previous_draw_date}")
        logger.info(f"Date range: {start_datetime} to {end_datetime}")
        
        # CRITICAL: Always ensure at least 4 questions exist for the next draw
        # If pool is empty or has less than 4, add questions
        if next_pool_count == 0:
            logger.warning("⚠️  WARNING: Next draw's pool is EMPTY! Adding questions immediately...")
        elif next_pool_count < 4:
            logger.warning(f"⚠️  Next draw's pool has only {next_pool_count} questions (need 4). Adding more...")
        
        # ALWAYS ensure we have at least 4 questions for the next draw
        if next_pool_count < 4:
            questions_needed = 4 - next_pool_count
            logger.info(f"Need to add {questions_needed} questions to next draw's pool")
            
            # Get existing question numbers in next draw's pool to avoid duplicates
            existing_numbers = [q.question_number for q in db.query(TriviaQuestionsDaily.question_number).filter(
                TriviaQuestionsDaily.date >= start_datetime,
                TriviaQuestionsDaily.date <= end_datetime
            ).all()]
            logger.debug(f"Existing question numbers in pool: {existing_numbers}")
            
            # Get unused questions (not in today's pool)
            unused_questions = db.query(Trivia).filter(
                Trivia.question_done == False,
                ~Trivia.question_number.in_(existing_numbers) if existing_numbers else True
            ).order_by(func.random()).limit(questions_needed).all()
            
            logger.debug(f"Found {len(unused_questions)} unused questions (need {questions_needed})")
            
            # If not enough questions, reset all questions
            if len(unused_questions) < questions_needed:
                total_questions = db.query(Trivia).count()
                logger.warning(f"Not enough unused questions. Total in DB: {total_questions}, Need: {questions_needed}")
                
                if total_questions < 4:
                    error_msg = f"CRITICAL ERROR: Only {total_questions} questions in database (need at least 4)"
                    logger.error(f"❌ {error_msg}")
                    raise Exception(error_msg)
                else:
                    # Reset all questions
                    logger.warning("⚠️  Not enough unused questions. Resetting ALL questions in trivia table...")
                    reset_count = db.query(Trivia).update({Trivia.question_done: False, Trivia.que_displayed_date: None})
                    db.commit()
                    logger.info(f"Reset {reset_count} questions in trivia table")
                    
                    # Get questions again (excluding existing)
                    unused_questions = db.query(Trivia).filter(
                        Trivia.question_done == False,
                        ~Trivia.question_number.in_(existing_numbers) if existing_numbers else True
                    ).order_by(func.random()).limit(questions_needed).all()
                    
                    logger.debug(f"After reset: Found {len(unused_questions)} unused questions (need {questions_needed})")
                    
                    if len(unused_questions) < questions_needed:
                        error_msg = f"CRITICAL ERROR: Still not enough questions after reset. Got {len(unused_questions)}, need {questions_needed}"
                        logger.error(f"❌ {error_msg}")
                        raise Exception(error_msg)
            
            # Determine starting order (max existing order + 1, or 1 if empty) - use timezone-aware date range
            max_existing_order = db.query(func.max(TriviaQuestionsDaily.question_order)).filter(
                TriviaQuestionsDaily.date >= start_datetime,
                TriviaQuestionsDaily.date <= end_datetime
            ).scalar() or 0
            
            # Add new questions to pool
            added_count = 0
            logger.info(f"Attempting to add {questions_needed} questions. Available unused questions: {len(unused_questions)}")
            
            for i, q in enumerate(unused_questions):
                order = max_existing_order + i + 1
                if order > 4:
                    break
                
                # Check for unique constraint violations - use timezone-aware date range
                existing_order = db.query(TriviaQuestionsDaily).filter(
                    TriviaQuestionsDaily.date >= start_datetime,
                    TriviaQuestionsDaily.date <= end_datetime,
                    TriviaQuestionsDaily.question_order == order
                ).first()
                
                if existing_order:
                    logger.debug(f"Skipping order {order} - already exists")
                    continue  # Skip if order already taken
                
                existing_qnum = db.query(TriviaQuestionsDaily).filter(
                    TriviaQuestionsDaily.date >= start_datetime,
                    TriviaQuestionsDaily.date <= end_datetime,
                    TriviaQuestionsDaily.question_number == q.question_number
                ).first()
                
                if existing_qnum:
                    logger.debug(f"Skipping question {q.question_number} - already in pool")
                    continue  # Skip if question already in pool
                
                # Create timezone-aware datetime for the next draw's date
                timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
                tz = pytz.timezone(timezone_str)
                date_datetime = tz.localize(datetime.combine(next_draw_date, datetime.min.time()))
                # Convert to UTC and remove timezone info for database storage
                date_utc = date_datetime.astimezone(pytz.UTC).replace(tzinfo=None)
                
                try:
                    dq = TriviaQuestionsDaily(
                        date=date_utc,
                        question_number=q.question_number,
                        question_order=order,
                        is_common=(order == 1),
                        is_used=False
                    )
                    db.add(dq)
                    logger.info(f"Adding question {q.question_number} as order {order} for date {date_utc}")
                    
                    # Mark question as used in trivia table
                    q.question_done = True
                    q.que_displayed_date = datetime.utcnow()
                    
                    added_count += 1
                except Exception as add_error:
                    logger.error(f"Failed to add question {q.question_number}: {add_error}")
                    db.rollback()
                    raise
            
            if added_count == 0:
                error_msg = f"CRITICAL: No questions were added! Tried to add {questions_needed} but added 0."
                logger.error(f"❌ {error_msg}")
                logger.error(f"Unused questions available: {len(unused_questions)}")
                logger.error(f"Existing numbers in pool: {existing_numbers}")
                raise Exception(error_msg)
            
            db.commit()
            logger.info(f"✅ Added {added_count} new questions to next draw's pool")
            
            # Verify questions were added
            final_count = db.query(TriviaQuestionsDaily).filter(
                TriviaQuestionsDaily.date >= start_datetime,
                TriviaQuestionsDaily.date <= end_datetime
            ).count()
            logger.info(f"✅ Final pool count: {final_count} questions")
            
            # Get the actual questions that were added for verification
            added_questions = db.query(TriviaQuestionsDaily).filter(
                TriviaQuestionsDaily.date >= start_datetime,
                TriviaQuestionsDaily.date <= end_datetime
            ).all()
            logger.debug(f"Questions in pool: {[(q.question_number, q.question_order) for q in added_questions]}")
            
            if final_count == 0:
                error_msg = "CRITICAL: Questions were not added to pool! Pool is still empty."
                logger.error(f"❌ {error_msg}")
                raise Exception(error_msg)
            elif final_count < 4:
                logger.warning(f"⚠️  WARNING: Pool has {final_count} questions (less than 4). This may cause issues.")
        else:
            logger.info(f"✅ Next draw's pool already has {next_pool_count} questions (sufficient)")
        
    except Exception as e:
        db.rollback()
        logger.error(f"❌ ERROR cleaning up questions: {e}", exc_info=True)
        raise
    finally:
        db.close()

if __name__ == "__main__":
    cleanup_unused_questions()
