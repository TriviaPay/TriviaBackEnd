"""
Service for handling CSV question uploads.
"""
import csv
import io
import json
from typing import List, Dict, Any
from sqlalchemy.orm import Session
from models import TriviaQuestionsFreeMode, TriviaQuestionsFiveDollarMode
from utils.question_hash_utils import generate_question_hash, check_duplicate_in_mode


def parse_csv_questions(file_content: bytes, mode_id: str) -> List[Dict[str, Any]]:
    """
    Parse CSV file content into question dictionaries.
    
    Expected CSV columns (matching Trivia table):
    - question, option_a, option_b, option_c, option_d, correct_answer
    - fill_in_answer (optional), hint (optional), explanation (optional)
    - category, country (optional), difficulty_level, picture_url (optional)
    
    Args:
        file_content: Bytes content of the CSV file
        mode_id: The mode identifier (e.g., 'free_mode')
        
    Returns:
        List of question dictionaries
        
    Raises:
        ValueError: If CSV format is invalid
    """
    try:
        # Decode bytes to string
        content = file_content.decode('utf-8')
        csv_file = io.StringIO(content)
        
        # Read CSV
        reader = csv.DictReader(csv_file)
        questions = []
        
        required_fields = ['question', 'option_a', 'option_b', 'option_c', 'option_d', 
                          'correct_answer', 'category', 'difficulty_level']
        
        for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
            # Check required fields
            missing_fields = [field for field in required_fields if not row.get(field)]
            if missing_fields:
                raise ValueError(f"Row {row_num}: Missing required fields: {', '.join(missing_fields)}")
            
            question_data = {
                'question': row['question'].strip(),
                'option_a': row['option_a'].strip(),
                'option_b': row['option_b'].strip(),
                'option_c': row['option_c'].strip(),
                'option_d': row['option_d'].strip(),
                'correct_answer': row['correct_answer'].strip(),
                'category': row['category'].strip(),
                'difficulty_level': row['difficulty_level'].strip(),
                'fill_in_answer': row.get('fill_in_answer', '').strip() or None,
                'hint': row.get('hint', '').strip() or None,
                'explanation': row.get('explanation', '').strip() or None,
                'country': row.get('country', '').strip() or None,
                'picture_url': row.get('picture_url', '').strip() or None,
            }
            
            questions.append(question_data)
        
        return questions
        
    except UnicodeDecodeError as e:
        raise ValueError(f"Invalid file encoding: {str(e)}")
    except Exception as e:
        raise ValueError(f"Error parsing CSV: {str(e)}")


def validate_question(question_data: Dict[str, Any]) -> tuple[bool, str]:
    """
    Validate question data.
    
    Args:
        question_data: Dictionary containing question fields
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Check required fields
    required_fields = ['question', 'option_a', 'option_b', 'option_c', 'option_d', 
                      'correct_answer', 'category', 'difficulty_level']
    
    for field in required_fields:
        if not question_data.get(field):
            return False, f"Missing required field: {field}"
    
    # Validate correct_answer is one of the options
    correct = question_data['correct_answer'].strip().upper()
    options = [
        question_data['option_a'].strip().upper(),
        question_data['option_b'].strip().upper(),
        question_data['option_c'].strip().upper(),
        question_data['option_d'].strip().upper(),
    ]
    
    if correct not in options:
        return False, f"correct_answer '{question_data['correct_answer']}' must match one of the options"
    
    return True, ""


def save_questions_to_mode(db: Session, questions: List[Dict[str, Any]], mode_id: str) -> Dict[str, Any]:
    """
    Save questions to the appropriate mode table.
    Validates questions, checks for duplicates, and inserts them.
    
    Args:
        db: Database session
        questions: List of question dictionaries
        mode_id: The mode identifier (e.g., 'free_mode')
        
    Returns:
        Dictionary with 'saved_count', 'duplicate_count', 'error_count', and 'errors'
    """
    saved_count = 0
    duplicate_count = 0
    error_count = 0
    errors = []
    
    for idx, question_data in enumerate(questions, start=1):
        try:
            # Validate question
            is_valid, error_msg = validate_question(question_data)
            if not is_valid:
                error_count += 1
                errors.append(f"Question {idx}: {error_msg}")
                continue
            
            # Generate hash
            question_hash = generate_question_hash(question_data['question'])
            
            # Check for duplicate
            if check_duplicate_in_mode(db, question_hash, mode_id):
                duplicate_count += 1
                errors.append(f"Question {idx}: Duplicate question found (hash: {question_hash[:8]}...)")
                continue
            
            # Save to appropriate table
            question_model = None
            if mode_id == 'free_mode':
                question_model = TriviaQuestionsFreeMode
            elif mode_id == 'five_dollar_mode':
                question_model = TriviaQuestionsFiveDollarMode
            else:
                error_count += 1
                errors.append(f"Question {idx}: Unknown mode_id '{mode_id}'")
                continue
            
            question = question_model(
                question=question_data['question'],
                option_a=question_data['option_a'],
                option_b=question_data['option_b'],
                option_c=question_data['option_c'],
                option_d=question_data['option_d'],
                correct_answer=question_data['correct_answer'],
                fill_in_answer=question_data.get('fill_in_answer'),
                hint=question_data.get('hint'),
                explanation=question_data.get('explanation'),
                category=question_data['category'],
                country=question_data.get('country'),
                difficulty_level=question_data['difficulty_level'],
                picture_url=question_data.get('picture_url'),
                question_hash=question_hash,
                is_used=False
            )
            db.add(question)
            saved_count += 1
        
        except Exception as e:
            error_count += 1
            errors.append(f"Question {idx}: Error saving - {str(e)}")
    
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise ValueError(f"Error committing questions to database: {str(e)}")
    
    return {
        'saved_count': saved_count,
        'duplicate_count': duplicate_count,
        'error_count': error_count,
        'errors': errors
    }

