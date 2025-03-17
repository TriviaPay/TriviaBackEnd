from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from db import get_db
from models import Winner

router = APIRouter(prefix="/winners", tags=["Winners"])

@router.get("/")
def get_recent_winners(db: Session = Depends(get_db)):
    """
    Endpoint to fetch recent winners.
    Fetches up to 5 most recent winners from the database.
    """
    winners = db.query(Winner).order_by(Winner.win_date.desc()).limit(5).all()
    return {
        "winners": [
            {
                "account_id": w.account_id,
                "amount_won": w.amount_won,
                "win_date": w.win_date,
                "profile_pic_url": w.user.profile_pic_url if w.user else None,
            }
            for w in winners
        ]
    }
