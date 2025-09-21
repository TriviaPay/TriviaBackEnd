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
    share_text: Optional[str] = None    # Share text (optional)
    app_link: Optional[str] = None      # App link (optional)

@router.get("/")
def get_live_update(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Endpoint to fetch the latest live update.
    Returns the most recent video URL, description, and share information.
    """
    update = db.query(LiveUpdate).order_by(LiveUpdate.created_date.desc()).first()
    if update:
        return {
            "video_url": update.video_url,
            "description": update.description,
            "share_text": update.share_text,
            "app_link": update.app_link,
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
    Share text and app link are optional - if not provided, previous values are kept.
    """
    existing_update = db.query(LiveUpdate).order_by(LiveUpdate.created_date.desc()).first()

    if existing_update:
        # Update the existing record
        existing_update.video_url = update_request.video_url
        existing_update.description = update_request.description
        existing_update.created_date = datetime.utcnow()
        
        # Only update share fields if provided
        if update_request.share_text is not None:
            existing_update.share_text = update_request.share_text
        if update_request.app_link is not None:
            existing_update.app_link = update_request.app_link
    else:
        # Create a new record
        new_update = LiveUpdate(
            video_url=update_request.video_url,
            description=update_request.description,
            share_text=update_request.share_text,
            app_link=update_request.app_link,
            created_date=datetime.utcnow(),
        )
        db.add(new_update)
    
    db.commit()
    return {
        "message": "Live update successfully updated", 
        "video_url": update_request.video_url,
        "share_text": update_request.share_text,
        "app_link": update_request.app_link
    }

@router.get("/share")
def get_share_info(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Endpoint to get share information for the latest update.
    Returns video URL, share text, and app link for sharing.
    """
    update = db.query(LiveUpdate).order_by(LiveUpdate.created_date.desc()).first()
    if update:
        return {
            "video_url": update.video_url,
            "share_text": update.share_text,
            "app_link": update.app_link,
            "description": update.description,
        }
    return {"message": "No live updates available for sharing."}
