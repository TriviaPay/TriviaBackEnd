from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from db import get_db
from models import Trivia

router = APIRouter(prefix="/trivia", tags=["Trivia"])

@router.get("/")
def get_trivia_questions(db: Session = Depends(get_db)):
    """
    Endpoint to fetch trivia questions.
    Fetches active trivia questions from the database.
    """
    questions = db.query(Trivia).filter(Trivia.status_flag == "Active").all()
    return {
        "questions": [
            {
                "question_number": q.question_number,
                "question": q.question,
                "options": [q.option_a, q.option_b, q.option_c, q.option_d],
                "category": q.category,
                "difficulty_level": q.difficulty_level,
            }
            for q in questions
        ]
    }

@router.get("/countries")
def get_countries(db: Session = Depends(get_db)):
    """
    Fetch distinct countries from the trivia table.
    """
    countries = db.query(Trivia.country).distinct().all()
    return {"countries": [c[0] for c in countries if c[0] is not None]}

@router.get("/categories")
def get_categories(db: Session = Depends(get_db)):
    """
    Fetch distinct categories from the trivia table.
    """
    categories = db.query(Trivia.category).distinct().all()
    return {"categories": [c[0] for c in categories if c[0] is not None]}

