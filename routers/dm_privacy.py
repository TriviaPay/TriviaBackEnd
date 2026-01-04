from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime
from typing import List
import logging

from db import get_db
from models import User, Block
from routers.dependencies import get_current_user
from config import E2EE_DM_ENABLED

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dm", tags=["DM Privacy"])


class BlockUserRequest(BaseModel):
    blocked_user_id: int = Field(..., description="User ID to block", example=1142961859)
    
    class Config:
        json_schema_extra = {
            "example": {
                "blocked_user_id": 1142961859
            }
        }


@router.post("/block")
def block_user(
    request: BlockUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Block a user. Prevents them from messaging you and seeing your key bundles.
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")
    
    if request.blocked_user_id == current_user.account_id:
        raise HTTPException(status_code=400, detail="Cannot block yourself")
    
    # Validate blocked user exists
    blocked_user = db.query(User).filter(User.account_id == request.blocked_user_id).first()
    if not blocked_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check if already blocked
    existing_block = db.query(Block).filter(
        Block.blocker_id == current_user.account_id,
        Block.blocked_id == request.blocked_user_id
    ).first()
    
    if existing_block:
        return {
            "success": True,
            "message": "User already blocked"
        }
    
    # Create block
    new_block = Block(
        blocker_id=current_user.account_id,
        blocked_id=request.blocked_user_id,
        created_at=datetime.utcnow()
    )
    
    try:
        db.add(new_block)
        db.commit()
    except IntegrityError:
        db.rollback()
        return {
            "success": True,
            "message": "User already blocked"
        }
    
    logger.info(f"User {current_user.account_id} blocked user {request.blocked_user_id}")
    
    return {
        "success": True,
        "message": "User blocked successfully"
    }


@router.delete("/block/{blocked_user_id}")
def unblock_user(
    blocked_user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Unblock a user.
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")
    
    block = db.query(Block).filter(
        Block.blocker_id == current_user.account_id,
        Block.blocked_id == blocked_user_id
    ).first()
    
    if not block:
        raise HTTPException(status_code=404, detail="User is not blocked")
    
    db.delete(block)
    db.commit()
    
    logger.info(f"User {current_user.account_id} unblocked user {blocked_user_id}")
    
    return {
        "success": True,
        "message": "User unblocked successfully"
    }


@router.get("/blocks")
def list_blocks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    """
    List all users blocked by the current user.
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")
    
    rows = db.query(Block, User).join(
        User, User.account_id == Block.blocked_id
    ).filter(
        Block.blocker_id == current_user.account_id
    ).order_by(
        Block.created_at.desc()
    ).offset(offset).limit(limit).all()
    
    blocked_users = []
    for block, blocked_user in rows:
        blocked_users.append({
            "user_id": blocked_user.account_id,
            "username": blocked_user.username,
            "blocked_at": block.created_at.isoformat()
        })
    
    return {
        "blocked_users": blocked_users
    }
