from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from db import get_db
from models import TriviaQuestionsEntries, User
from routers.dependencies import get_current_user

router = APIRouter(prefix="/entries", tags=["Entries"])

@router.get("/")
def get_all_entries(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Endpoint to fetch all entries.
    Returns the question entry stats for all users.
    """
    entries = db.query(TriviaQuestionsEntries).all()  # Fetch all records from TriviaQuestionsEntries table
    
    if not entries:
        return {"message": "No Entries available."}

    return {
        "entries": [
            {
                "account_id": e.account_id,
                "ques_attempted": e.ques_attempted,
                "correct_answers": e.correct_answers,
                "wrong_answers": e.wrong_answers,
                "date": e.date.isoformat()
            }
            for e in entries
        ]
    }
