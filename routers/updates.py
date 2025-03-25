from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from db import get_db
from models import LiveUpdate, User
from routers.dependencies import get_current_user
from datetime import datetime
from typing import Optional


router = APIRouter(prefix="/updates", tags=["Updates"])

# Pydantic schema for request body validation
class LiveUpdateRequest(BaseModel):
    video_url: str
    description: Optional[str] = None   # Description is optional

@router.get("/")
def get_live_update(db: Session = Depends(get_db),user: User = Depends(get_current_user)):
    """
    Endpoint to fetch the latest live update.
    Returns the most recent video URL and description.
    """
    update = db.query(LiveUpdate).order_by(LiveUpdate.created_date.desc()).first()
    if update:
        return {
            "video_url": update.video_url,
            "description": update.description,
            "created_date": update.created_date,
        }
    return {"message": "No live updates available."}

@router.post("/")
def update_live_update(
    update_request: LiveUpdateRequest, 
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """
    Endpoint to update the latest live update video URL.
    If an update already exists, it updates it; otherwise, it creates a new entry.
    """
    existing_update = db.query(LiveUpdate).order_by(LiveUpdate.created_date.desc()).first()

    if existing_update:
        # Update the existing record
        existing_update.video_url = update_request.video_url
        existing_update.description = update_request.description
        existing_update.created_date = datetime.utcnow()
    else:
        # Create a new record
        new_update = LiveUpdate(
            video_url=update_request.video_url,
            description=update_request.description,
            created_date=datetime.utcnow(),
        )
        db.add(new_update)
    
    db.commit()
    return {"message": "Live update successfully updated", "video_url": update_request.video_url}
