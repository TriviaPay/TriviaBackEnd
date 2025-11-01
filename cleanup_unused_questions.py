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

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import SessionLocal
from models import TriviaQuestionsDaily, Trivia

def cleanup_unused_questions():
    """
    Clean up unused daily questions and refresh the pool for today.
    This should be run daily (e.g., after draw) to manage question allocation.
    """
    db: Session = SessionLocal()
    
    try:
        today = date.today()
        yesterday = today - timedelta(days=1)
        
        # Step 1: Delete unused questions from yesterday
        yesterday_unused = db.query(TriviaQuestionsDaily).filter(
            func.date(TriviaQuestionsDaily.date) == yesterday,
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
        
        # Step 3: Check today's pool and add questions if needed
        today_pool_count = db.query(TriviaQuestionsDaily).filter(
            func.date(TriviaQuestionsDaily.date) == today
        ).count()
        
        print(f"Today's pool has {today_pool_count} questions")
        
        if today_pool_count < 4:
            questions_needed = 4 - today_pool_count
            
            # Get existing question numbers in today's pool to avoid duplicates
            existing_numbers = [q.question_number for q in db.query(TriviaQuestionsDaily.question_number).filter(
                func.date(TriviaQuestionsDaily.date) == today
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
            
            # Determine starting order (max existing order + 1, or 1 if empty)
            max_existing_order = db.query(func.max(TriviaQuestionsDaily.question_order)).filter(
                func.date(TriviaQuestionsDaily.date) == today
            ).scalar() or 0
            
            # Add new questions to pool
            added_count = 0
            for i, q in enumerate(unused_questions):
                order = max_existing_order + i + 1
                if order > 4:
                    break
                
                # Check for unique constraint violations
                existing_order = db.query(TriviaQuestionsDaily).filter(
                    func.date(TriviaQuestionsDaily.date) == today,
                    TriviaQuestionsDaily.question_order == order
                ).first()
                
                if existing_order:
                    continue  # Skip if order already taken
                
                existing_qnum = db.query(TriviaQuestionsDaily).filter(
                    func.date(TriviaQuestionsDaily.date) == today,
                    TriviaQuestionsDaily.question_number == q.question_number
                ).first()
                
                if existing_qnum:
                    continue  # Skip if question already in pool
                
                dq = TriviaQuestionsDaily(
                    date=datetime.combine(today, datetime.min.time()),
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
