#!/usr/bin/env python3
"""
Clean up unused daily questions and mark trivia questions as unused
"""

import sys
import os
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, date

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db import SessionLocal
from models import TriviaQuestionsDaily, Trivia

def cleanup_unused_questions():
    """
    Clean up unused daily questions and mark trivia questions as unused.
    This should be run after each draw to free up unused questions.
    """
    db: Session = SessionLocal()
    
    try:
        today = date.today()
        
        # Get all daily questions from today
        today_questions = db.query(TriviaQuestionsDaily).filter(
            func.date(TriviaQuestionsDaily.date) == today
        ).all()
        
        # Count total, used, and unused questions
        total_questions = len(today_questions)
        used_questions = sum(1 for q in today_questions if q.is_used)
        unused_questions = total_questions - used_questions
        
        print(f"Found {total_questions} questions for today, {used_questions} used and {unused_questions} unused")
        
        # For each unused question, mark the Trivia question as undone
        # and delete the daily question record
        cleaned_count = 0
        for daily_q in today_questions:
            if not daily_q.is_used:
                # Get the Trivia question
                trivia_q = db.query(Trivia).filter(
                    Trivia.question_number == daily_q.question_number
                ).first()
                
                if trivia_q:
                    # Mark it as undone so it can be used again
                    trivia_q.question_done = False
                    trivia_q.que_displayed_date = None
                    print(f"Marked question {trivia_q.question_number} as undone")
                
                # Delete the daily question record
                db.delete(daily_q)
                cleaned_count += 1
        
        db.commit()
        print(f"Successfully cleaned up {cleaned_count} unused questions")
        
    except Exception as e:
        db.rollback()
        print(f"Error cleaning up questions: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    cleanup_unused_questions()
