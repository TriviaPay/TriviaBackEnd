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
    Returns the number_of_entries for all users.
    """
    entries = db.query(TriviaQuestionsEntries).all()  # Fetch all records from TriviaQuestionsEntries table
    
    if not entries:
        return {"message": "No Entries available."}

    return {
        "entries": [
            {"account_id": e.account_id, "number_of_entries": e.number_of_entries}
            for e in entries
        ]
    }
