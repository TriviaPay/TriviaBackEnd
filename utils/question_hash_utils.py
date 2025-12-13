"""
Utility functions for question hash generation and duplicate checking.
"""
import hashlib
from sqlalchemy.orm import Session
from models import Trivia, TriviaQuestionsFreeMode, TriviaQuestionsFiveDollarMode


def generate_question_hash(question_text: str) -> str:
    """
    Generate MD5 hash of question text for deduplication.
    
    Args:
        question_text: The question text to hash
        
    Returns:
        MD5 hash string (hexdigest)
    """
    return hashlib.md5(question_text.encode('utf-8')).hexdigest()


def check_duplicate_across_modes(db: Session, question_hash: str) -> dict:
    """
    Check if a question with the given hash exists in any mode table.
    
    Args:
        db: Database session
        question_hash: MD5 hash of the question text
        
    Returns:
        Dictionary with 'exists' (bool) and 'found_in' (list of table names)
    """
    found_in = []
    
    # Note: Main Trivia table doesn't have question_hash column yet
    # To check duplicates in main Trivia table, we would need to:
    # 1. Add question_hash column to Trivia table, or
    # 2. Generate hash for each question on-the-fly (slow)
    # For now, we only check mode-specific tables that have question_hash
    
    # Check free mode questions table
    existing_free = db.query(TriviaQuestionsFreeMode).filter(
        TriviaQuestionsFreeMode.question_hash == question_hash
    ).first()
    
    if existing_free:
        found_in.append('trivia_questions_free_mode')
    
    # Check $5 mode questions table
    existing_five_dollar = db.query(TriviaQuestionsFiveDollarMode).filter(
        TriviaQuestionsFiveDollarMode.question_hash == question_hash
    ).first()
    
    if existing_five_dollar:
        found_in.append('trivia_questions_five_dollar_mode')
    
    # Add more mode tables here as they are created
    
    return {
        'exists': len(found_in) > 0,
        'found_in': found_in
    }


def check_duplicate_in_mode(db: Session, question_hash: str, mode_id: str) -> bool:
    """
    Check if a question with the given hash exists in a specific mode table.
    
    Args:
        db: Database session
        question_hash: MD5 hash of the question text
        mode_id: The mode identifier (e.g., 'free_mode')
        
    Returns:
        True if duplicate exists, False otherwise
    """
    if mode_id == 'free_mode':
        existing = db.query(TriviaQuestionsFreeMode).filter(
            TriviaQuestionsFreeMode.question_hash == question_hash
        ).first()
        return existing is not None
    
    if mode_id == 'five_dollar_mode':
        existing = db.query(TriviaQuestionsFiveDollarMode).filter(
            TriviaQuestionsFiveDollarMode.question_hash == question_hash
        ).first()
        return existing is not None
    
    # Add more modes here as they are created
    return False

