#!/usr/bin/env python3
"""
Clean up unused daily questions and refresh daily question pool.
New logic:
1. Delete rows from trivia_questions_daily where date=yesterday AND is_used=false
2. Reset those questions in trivia table
3. Check today's pool count, add questions if < 4
"""

import sys
import os
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, date, timedelta
import pytz

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import SessionLocal
from models import TriviaQuestionsDaily, Trivia

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
    Clean up unused daily questions and refresh the pool for today.
    This should be run daily (e.g., after draw) to manage question allocation.
    """
    db: Session = SessionLocal()
    
    try:
        today = get_today_in_app_timezone()
        yesterday = today - timedelta(days=1)
        
        # Step 1: Delete unused questions from yesterday - use timezone-aware date range
        start_datetime, end_datetime = get_date_range_for_query(yesterday)
        yesterday_unused = db.query(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= start_datetime,
            TriviaQuestionsDaily.date <= end_datetime,
            TriviaQuestionsDaily.is_used == False
        ).all()
        
        deleted_count = 0
        question_numbers_to_reset = []
        
        for daily_q in yesterday_unused:
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
            print(f"Reset {len(question_numbers_to_reset)} questions in trivia table")
        
        db.commit()
        print(f"Deleted {deleted_count} unused questions from {yesterday}")
        
        # Step 3: Check today's pool and add questions if needed - use timezone-aware date range
        start_datetime, end_datetime = get_date_range_for_query(today)
        today_pool_count = db.query(TriviaQuestionsDaily).filter(
            TriviaQuestionsDaily.date >= start_datetime,
            TriviaQuestionsDaily.date <= end_datetime
        ).count()
        
        print(f"Today's pool has {today_pool_count} questions")
        
        if today_pool_count < 4:
            questions_needed = 4 - today_pool_count
            
            # Get existing question numbers in today's pool to avoid duplicates
            existing_numbers = [q.question_number for q in db.query(TriviaQuestionsDaily.question_number).filter(
                TriviaQuestionsDaily.date >= start_datetime,
                TriviaQuestionsDaily.date <= end_datetime
            ).all()]
            
            # Get unused questions (not in today's pool)
            unused_questions = db.query(Trivia).filter(
                Trivia.question_done == False,
                ~Trivia.question_number.in_(existing_numbers) if existing_numbers else True
            ).order_by(func.random()).limit(questions_needed).all()
            
            # If not enough questions, reset all questions
            if len(unused_questions) < questions_needed:
                total_questions = db.query(Trivia).count()
                if total_questions < 4:
                    print(f"Warning: Only {total_questions} questions in database (need at least 4)")
                else:
                    # Reset all questions
                    db.query(Trivia).update({Trivia.question_done: False, Trivia.que_displayed_date: None})
                    db.commit()
                    
                    # Get questions again (excluding existing)
                    unused_questions = db.query(Trivia).filter(
                        Trivia.question_done == False,
                        ~Trivia.question_number.in_(existing_numbers) if existing_numbers else True
                    ).order_by(func.random()).limit(questions_needed).all()
            
            # Determine starting order (max existing order + 1, or 1 if empty) - use timezone-aware date range
            max_existing_order = db.query(func.max(TriviaQuestionsDaily.question_order)).filter(
                TriviaQuestionsDaily.date >= start_datetime,
                TriviaQuestionsDaily.date <= end_datetime
            ).scalar() or 0
            
            # Add new questions to pool
            added_count = 0
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
                    continue  # Skip if order already taken
                
                existing_qnum = db.query(TriviaQuestionsDaily).filter(
                    TriviaQuestionsDaily.date >= start_datetime,
                    TriviaQuestionsDaily.date <= end_datetime,
                    TriviaQuestionsDaily.question_number == q.question_number
                ).first()
                
                if existing_qnum:
                    continue  # Skip if question already in pool
                
                # Create timezone-aware datetime for the date
                timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
                tz = pytz.timezone(timezone_str)
                date_datetime = tz.localize(datetime.combine(today, datetime.min.time()))
                # Convert to UTC and remove timezone info for database storage
                date_utc = date_datetime.astimezone(pytz.UTC).replace(tzinfo=None)
                
                dq = TriviaQuestionsDaily(
                    date=date_utc,
                    question_number=q.question_number,
                    question_order=order,
                    is_common=(order == 1),
                    is_used=False
                )
                db.add(dq)
                
                # Mark question as used in trivia table
                q.question_done = True
                q.que_displayed_date = datetime.utcnow()
                
                added_count += 1
            
            db.commit()
            print(f"Added {added_count} new questions to today's pool")
        
    except Exception as e:
        db.rollback()
        print(f"Error cleaning up questions: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    cleanup_unused_questions()
