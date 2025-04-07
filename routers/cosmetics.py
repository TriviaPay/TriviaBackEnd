from fastapi import APIRouter, Depends, HTTPException, status, Request, Body, Path, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import desc
import json
import uuid
import logging
import os
from datetime import datetime
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from db import get_db
from models import User, Avatar, Frame, UserAvatar, UserFrame
from routers.dependencies import get_current_user

# Load environment variables
load_dotenv()

router = APIRouter(prefix="/cosmetics", tags=["Cosmetics"])

# ======== Helper Functions ========

def is_admin(current_user: dict, db: Session) -> bool:
    """
    Check if the current user is an admin based on their email matching ADMIN_EMAIL in env
    
    Args:
        current_user (dict): The current user's JWT claims
        db (Session): Database session
        
    Returns:
        bool: Whether the user is an admin
    """
    # Get admin email from environment or use default
    admin_email = os.getenv("ADMIN_EMAIL", "triviapay3@gmail.com")
    
    # Admin check is based on email
    email = current_user.get('email')
    if email and email.lower() == admin_email.lower():
        return True
        
    # Check in database
    if email:
        user = db.query(User).filter(User.email == email).first()
        if user and user.email.lower() == admin_email.lower():
            return True
            
    return False
    
def verify_admin(current_user: dict, db: Session) -> None:
    """
    Verify the user is an admin or raise an HTTP exception
    
    Args:
        current_user (dict): The current user's JWT claims
        db (Session): Database session
        
    Raises:
        HTTPException: If the user is not an admin
    """
    if not is_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required for this endpoint"
        )

# ======== Pydantic Models for Request/Response Validation ========

class CosmeticBase(BaseModel):
    name: str
    description: Optional[str] = None
    image_url: str
    price_gems: Optional[int] = None
    price_usd: Optional[float] = None
    is_premium: bool = False

class AvatarCreate(CosmeticBase):
    """Schema for creating a new avatar"""
    id: Optional[str] = None

class AvatarResponse(CosmeticBase):
    """Schema for avatar response"""
    id: str
    created_at: datetime
    
    class Config:
        orm_mode = True

class FrameCreate(CosmeticBase):
    """Schema for creating a new frame"""
    id: Optional[str] = None

class FrameResponse(CosmeticBase):
    """Schema for frame response"""
    id: str
    created_at: datetime
    
    class Config:
        orm_mode = True

class UserCosmeticResponse(BaseModel):
    """Schema for user-owned cosmetics response"""
    id: str
    name: str
    description: Optional[str]
    image_url: str
    is_premium: bool
    purchase_date: datetime
    
    class Config:
        orm_mode = True

class PurchaseResponse(BaseModel):
    status: str
    message: str
    item_id: str
    purchase_date: datetime
    gems_spent: Optional[int] = None
    usd_spent: Optional[float] = None

class SelectResponse(BaseModel):
    status: str
    message: str
    selected_id: str

# More endpoints will be added soon

# ======== Avatar Endpoints ========

@router.get("/avatars", response_model=List[AvatarResponse])
async def get_all_avatars(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    skip: int = 0,
    limit: int = 100
):
    """
    Get all available avatars, ordered by most recently added first
    """
    avatars = db.query(Avatar).order_by(desc(Avatar.created_at)).offset(skip).limit(limit).all()
    return avatars

@router.get("/avatars/owned", response_model=List[UserCosmeticResponse])
async def get_user_avatars(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Get all avatars owned by the current user
    """
    # Find the user
    user = db.query(User).filter(User.sub == current_user['sub']).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get user avatars with joined data
    user_avatars = db.query(
        Avatar.id, 
        Avatar.name, 
        Avatar.description, 
        Avatar.image_url, 
        Avatar.is_premium, 
        UserAvatar.purchase_date
    ).join(
        UserAvatar, UserAvatar.avatar_id == Avatar.id
    ).filter(
        UserAvatar.user_id == user.account_id
    ).all()
    
    # Sort by purchase date, newest first
    result = list(user_avatars)
    result.sort(key=lambda x: x.purchase_date, reverse=True)
    
    return result

@router.post("/avatars/buy/{avatar_id}", response_model=PurchaseResponse)
async def buy_avatar(
    avatar_id: str = Path(..., description="The ID of the avatar to purchase"),
    payment_method: str = Query(..., description="Payment method: 'gems' or 'usd'"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Purchase an avatar using gems or USD
    """
    # Find the user
    user = db.query(User).filter(User.sub == current_user['sub']).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Find the avatar
    avatar = db.query(Avatar).filter(Avatar.id == avatar_id).first()
    if not avatar:
        raise HTTPException(status_code=404, detail=f"Avatar with ID {avatar_id} not found")
    
    # Check if user already owns this avatar
    existing = db.query(UserAvatar).filter(
        UserAvatar.user_id == user.account_id,
        UserAvatar.avatar_id == avatar_id
    ).first()
    
    if existing:
        return PurchaseResponse(
            status="error",
            message=f"You already own the avatar '{avatar.name}'",
            item_id=avatar_id,
            purchase_date=existing.purchase_date
        )
    
    # Process payment based on method
    if payment_method == "gems":
        # Check if the avatar can be purchased with gems
        if avatar.price_gems is None:
            raise HTTPException(
                status_code=400,
                detail=f"Avatar '{avatar.name}' cannot be purchased with gems"
            )
        
        # Check if user has enough gems
        if user.gems < avatar.price_gems:
            raise HTTPException(
                status_code=400,
                detail=f"Not enough gems. You have {user.gems} gems, but this avatar costs {avatar.price_gems} gems"
            )
        
        # Deduct gems from user
        user.gems -= avatar.price_gems
        
        # Create ownership record
        new_ownership = UserAvatar(
            user_id=user.account_id,
            avatar_id=avatar_id,
            purchase_date=datetime.utcnow()
        )
        
        db.add(new_ownership)
        db.commit()
        
        return PurchaseResponse(
            status="success",
            message=f"Successfully purchased avatar '{avatar.name}' for {avatar.price_gems} gems",
            item_id=avatar_id,
            purchase_date=new_ownership.purchase_date,
            gems_spent=avatar.price_gems
        )
    
    elif payment_method == "usd":
        # Check if the avatar can be purchased with USD
        if avatar.price_usd is None:
            raise HTTPException(
                status_code=400,
                detail=f"Avatar '{avatar.name}' cannot be purchased with USD"
            )
        
        # TODO: Implement actual payment processing
        # For now, just create the ownership record
        
        new_ownership = UserAvatar(
            user_id=user.account_id,
            avatar_id=avatar_id,
            purchase_date=datetime.utcnow()
        )
        
        db.add(new_ownership)
        db.commit()
        
        return PurchaseResponse(
            status="success",
            message=f"Successfully purchased avatar '{avatar.name}' for ${avatar.price_usd}",
            item_id=avatar_id,
            purchase_date=new_ownership.purchase_date,
            usd_spent=avatar.price_usd
        )
    
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid payment method: {payment_method}. Must be 'gems' or 'usd'"
        )

@router.post("/avatars/select/{avatar_id}", response_model=SelectResponse)
async def select_avatar(
    avatar_id: str = Path(..., description="The ID of the avatar to select"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Select an avatar as the current profile avatar
    """
    # Find the user
    user = db.query(User).filter(User.sub == current_user['sub']).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check if user owns this avatar or if it's a default avatar
    avatar = db.query(Avatar).filter(Avatar.id == avatar_id).first()
    if not avatar:
        raise HTTPException(status_code=404, detail=f"Avatar with ID {avatar_id} not found")
    
    ownership = db.query(UserAvatar).filter(
        UserAvatar.user_id == user.account_id,
        UserAvatar.avatar_id == avatar_id
    ).first()
    
    if not ownership and not avatar.is_default:
        raise HTTPException(
            status_code=403,
            detail=f"You don't own the avatar with ID {avatar_id}"
        )
    
    # Update the user's selected avatar
    user.selected_avatar_id = avatar_id
    db.commit()
    
    return SelectResponse(
        status="success",
        message=f"Successfully selected avatar '{avatar.name}' as your profile avatar",
        selected_id=avatar_id
    )

# Admin endpoint to add new avatars
@router.post("/admin/avatars", response_model=AvatarResponse)
async def create_avatar(
    avatar: AvatarCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Admin endpoint to create a new avatar
    """
    # Check admin access
    verify_admin(current_user, db)
    
    # Use provided ID or generate a new one
    avatar_id = avatar.id if avatar.id else str(uuid.uuid4())
    
    # Check if an avatar with this ID already exists
    if avatar.id:
        existing = db.query(Avatar).filter(Avatar.id == avatar_id).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Avatar with ID {avatar_id} already exists"
            )
    
    # Create a new avatar
    new_avatar = Avatar(
        id=avatar_id,
        name=avatar.name,
        description=avatar.description,
        image_url=avatar.image_url,
        price_gems=avatar.price_gems,
        price_usd=avatar.price_usd,
        is_premium=avatar.is_premium,
        created_at=datetime.utcnow()
    )
    
    db.add(new_avatar)
    db.commit()
    db.refresh(new_avatar)
    
    return new_avatar

# Admin endpoint to update an existing avatar
@router.put("/admin/avatars/{avatar_id}", response_model=AvatarResponse)
async def update_avatar(
    avatar_id: str = Path(..., description="The ID of the avatar to update"),
    avatar_update: AvatarCreate = Body(..., description="Updated avatar data"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Admin endpoint to update an existing avatar
    """
    # Check admin access
    verify_admin(current_user, db)
    
    # Find the avatar
    avatar = db.query(Avatar).filter(Avatar.id == avatar_id).first()
    if not avatar:
        raise HTTPException(status_code=404, detail=f"Avatar with ID {avatar_id} not found")
    
    # Update avatar fields
    avatar.name = avatar_update.name
    avatar.description = avatar_update.description
    avatar.image_url = avatar_update.image_url
    avatar.price_gems = avatar_update.price_gems
    avatar.price_usd = avatar_update.price_usd
    avatar.is_premium = avatar_update.is_premium
    
    db.commit()
    db.refresh(avatar)
    
    return avatar

# Admin endpoint to delete an avatar
@router.delete("/admin/avatars/{avatar_id}", response_model=dict)
async def delete_avatar(
    avatar_id: str = Path(..., description="The ID of the avatar to delete"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Admin endpoint to delete an avatar
    """
    # Check admin access
    verify_admin(current_user, db)
    
    # Find the avatar
    avatar = db.query(Avatar).filter(Avatar.id == avatar_id).first()
    if not avatar:
        raise HTTPException(status_code=404, detail=f"Avatar with ID {avatar_id} not found")
    
    # Remove any references in user_avatars table
    user_avatars = db.query(UserAvatar).filter(UserAvatar.avatar_id == avatar_id).all()
    for user_avatar in user_avatars:
        db.delete(user_avatar)
    
    # Remove any users who have this as selected avatar
    users_with_selected = db.query(User).filter(User.selected_avatar_id == avatar_id).all()
    for user in users_with_selected:
        user.selected_avatar_id = None
    
    # Delete the avatar
    db.delete(avatar)
    db.commit()
    
    return {"status": "success", "message": f"Avatar with ID {avatar_id} deleted successfully"}

# ======== Frame Endpoints ========

@router.get("/frames", response_model=List[FrameResponse])
async def get_all_frames(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    skip: int = 0,
    limit: int = 100
):
    """
    Get all available frames, ordered by most recently added first
    """
    frames = db.query(Frame).order_by(desc(Frame.created_at)).offset(skip).limit(limit).all()
    return frames

@router.get("/frames/owned", response_model=List[UserCosmeticResponse])
async def get_user_frames(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Get all frames owned by the current user
    """
    # Find the user
    user = db.query(User).filter(User.sub == current_user['sub']).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get user frames with joined data
    user_frames = db.query(
        Frame.id, 
        Frame.name, 
        Frame.description, 
        Frame.image_url, 
        Frame.is_premium, 
        UserFrame.purchase_date
    ).join(
        UserFrame, UserFrame.frame_id == Frame.id
    ).filter(
        UserFrame.user_id == user.account_id
    ).all()
    
    # Sort by purchase date, newest first
    result = list(user_frames)
    result.sort(key=lambda x: x.purchase_date, reverse=True)
    
    return result

@router.post("/frames/buy/{frame_id}", response_model=PurchaseResponse)
async def buy_frame(
    frame_id: str = Path(..., description="The ID of the frame to purchase"),
    payment_method: str = Query(..., description="Payment method: 'gems' or 'usd'"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Purchase a frame using gems or USD
    """
    # Find the user
    user = db.query(User).filter(User.sub == current_user['sub']).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Find the frame
    frame = db.query(Frame).filter(Frame.id == frame_id).first()
    if not frame:
        raise HTTPException(status_code=404, detail=f"Frame with ID {frame_id} not found")
    
    # Check if user already owns this frame
    existing = db.query(UserFrame).filter(
        UserFrame.user_id == user.account_id,
        UserFrame.frame_id == frame_id
    ).first()
    
    if existing:
        return PurchaseResponse(
            status="error",
            message=f"You already own the frame '{frame.name}'",
            item_id=frame_id,
            purchase_date=existing.purchase_date
        )
    
    # Process payment based on method
    if payment_method == "gems":
        # Check if the frame can be purchased with gems
        if frame.price_gems is None:
            raise HTTPException(
                status_code=400,
                detail=f"Frame '{frame.name}' cannot be purchased with gems"
            )
        
        # Check if user has enough gems
        if user.gems < frame.price_gems:
            raise HTTPException(
                status_code=400,
                detail=f"Not enough gems. You have {user.gems} gems, but this frame costs {frame.price_gems} gems"
            )
        
        # Deduct gems from user
        user.gems -= frame.price_gems
        
        # Create ownership record
        new_ownership = UserFrame(
            user_id=user.account_id,
            frame_id=frame_id,
            purchase_date=datetime.utcnow()
        )
        
        db.add(new_ownership)
        db.commit()
        
        return PurchaseResponse(
            status="success",
            message=f"Successfully purchased frame '{frame.name}' for {frame.price_gems} gems",
            item_id=frame_id,
            purchase_date=new_ownership.purchase_date,
            gems_spent=frame.price_gems
        )
    
    elif payment_method == "usd":
        # Check if the frame can be purchased with USD
        if frame.price_usd is None:
            raise HTTPException(
                status_code=400,
                detail=f"Frame '{frame.name}' cannot be purchased with USD"
            )
        
        # TODO: Implement actual payment processing
        # For now, just create the ownership record
        
        new_ownership = UserFrame(
            user_id=user.account_id,
            frame_id=frame_id,
            purchase_date=datetime.utcnow()
        )
        
        db.add(new_ownership)
        db.commit()
        
        return PurchaseResponse(
            status="success",
            message=f"Successfully purchased frame '{frame.name}' for ${frame.price_usd}",
            item_id=frame_id,
            purchase_date=new_ownership.purchase_date,
            usd_spent=frame.price_usd
        )
    
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid payment method: {payment_method}. Must be 'gems' or 'usd'"
        )

@router.post("/frames/select/{frame_id}", response_model=SelectResponse)
async def select_frame(
    frame_id: str = Path(..., description="The ID of the frame to select"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Select a frame as the current profile frame
    """
    # Find the user
    user = db.query(User).filter(User.sub == current_user['sub']).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check if user owns this frame or if it's a default frame
    frame = db.query(Frame).filter(Frame.id == frame_id).first()
    if not frame:
        raise HTTPException(status_code=404, detail=f"Frame with ID {frame_id} not found")
    
    ownership = db.query(UserFrame).filter(
        UserFrame.user_id == user.account_id,
        UserFrame.frame_id == frame_id
    ).first()
    
    if not ownership and not frame.is_default:
        raise HTTPException(
            status_code=403,
            detail=f"You don't own the frame with ID {frame_id}"
        )
    
    # Update the user's selected frame
    user.selected_frame_id = frame_id
    db.commit()
    
    return SelectResponse(
        status="success",
        message=f"Successfully selected frame '{frame.name}' as your profile frame",
        selected_id=frame_id
    )

# Admin endpoint to add new frames
@router.post("/admin/frames", response_model=FrameResponse)
async def create_frame(
    frame: FrameCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Admin endpoint to create a new frame
    """
    # Check admin access
    verify_admin(current_user, db)
    
    # Use provided ID or generate a new one
    frame_id = frame.id if frame.id else str(uuid.uuid4())
    
    # Check if a frame with this ID already exists
    if frame.id:
        existing = db.query(Frame).filter(Frame.id == frame_id).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Frame with ID {frame_id} already exists"
            )
    
    # Create a new frame
    new_frame = Frame(
        id=frame_id,
        name=frame.name,
        description=frame.description,
        image_url=frame.image_url,
        price_gems=frame.price_gems,
        price_usd=frame.price_usd,
        is_premium=frame.is_premium,
        created_at=datetime.utcnow()
    )
    
    db.add(new_frame)
    db.commit()
    db.refresh(new_frame)
    
    return new_frame

# Admin endpoint to update an existing frame
@router.put("/admin/frames/{frame_id}", response_model=FrameResponse)
async def update_frame(
    frame_id: str = Path(..., description="The ID of the frame to update"),
    frame_update: FrameCreate = Body(..., description="Updated frame data"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Admin endpoint to update an existing frame
    """
    # Check admin access
    verify_admin(current_user, db)
    
    # Find the frame
    frame = db.query(Frame).filter(Frame.id == frame_id).first()
    if not frame:
        raise HTTPException(status_code=404, detail=f"Frame with ID {frame_id} not found")
    
    # Update frame fields
    frame.name = frame_update.name
    frame.description = frame_update.description
    frame.image_url = frame_update.image_url
    frame.price_gems = frame_update.price_gems
    frame.price_usd = frame_update.price_usd
    frame.is_premium = frame_update.is_premium
    
    db.commit()
    db.refresh(frame)
    
    return frame

# Admin endpoint to delete a frame
@router.delete("/admin/frames/{frame_id}", response_model=dict)
async def delete_frame(
    frame_id: str = Path(..., description="The ID of the frame to delete"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Admin endpoint to delete a frame
    """
    # Check admin access
    verify_admin(current_user, db)
    
    # Find the frame
    frame = db.query(Frame).filter(Frame.id == frame_id).first()
    if not frame:
        raise HTTPException(status_code=404, detail=f"Frame with ID {frame_id} not found")
    
    # Remove any references in user_frames table
    user_frames = db.query(UserFrame).filter(UserFrame.frame_id == frame_id).all()
    for user_frame in user_frames:
        db.delete(user_frame)
    
    # Remove any users who have this as selected frame
    users_with_selected = db.query(User).filter(User.selected_frame_id == frame_id).all()
    for user in users_with_selected:
        user.selected_frame_id = None
    
    # Delete the frame
    db.delete(frame)
    db.commit()
    
    return {"status": "success", "message": f"Frame with ID {frame_id} deleted successfully"}

# ======== Bulk Import/Export Functions for Admin ========

class BulkImportResponse(BaseModel):
    status: str
    message: str
    imported_count: int
    errors: List[str] = []

@router.post("/admin/avatars/import", response_model=BulkImportResponse)
async def import_avatars_from_json(
    json_data: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Bulk import avatars from a JSON file or import a single avatar.
    Accepts either a single avatar object or an object with an "avatars" array.
    """
    # Check admin access
    verify_admin(current_user, db)
    
    # Check if this is a single avatar or a collection
    if "avatars" in json_data:
        avatars = json_data.get("avatars", [])
    elif "id" in json_data and "name" in json_data and "image_url" in json_data:
        # This is a single avatar
        avatars = [json_data]
    else:
        # No valid avatar data found
        avatars = []
    
    if not avatars:
        return BulkImportResponse(
            status="error",
            message="No avatars found in the JSON data",
            imported_count=0
        )
    
    imported = 0
    errors = []
    
    for avatar_data in avatars:
        try:
            # Generate a unique ID if not provided
            avatar_id = avatar_data.get("id", str(uuid.uuid4()))
            
            # Check if this avatar already exists
            existing = db.query(Avatar).filter(Avatar.id == avatar_id).first()
            if existing:
                # Update existing avatar
                for key, value in avatar_data.items():
                    if key != "id" and hasattr(existing, key):
                        setattr(existing, key, value)
            else:
                # Create new avatar
                new_avatar = Avatar(
                    id=avatar_id,
                    name=avatar_data.get("name", "Unnamed Avatar"),
                    description=avatar_data.get("description"),
                    image_url=avatar_data.get("image_url", ""),
                    price_gems=avatar_data.get("price_gems"),
                    price_usd=avatar_data.get("price_usd"),
                    is_premium=avatar_data.get("is_premium", False),
                    created_at=datetime.utcnow()
                )
                db.add(new_avatar)
            
            imported += 1
        except Exception as e:
            errors.append(f"Error importing avatar {avatar_data.get('name', 'unknown')}: {str(e)}")
    
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        return BulkImportResponse(
            status="error",
            message=f"Database error: {str(e)}",
            imported_count=0,
            errors=[str(e)]
        )
    
    return BulkImportResponse(
        status="success",
        message=f"Successfully imported {imported} avatars",
        imported_count=imported,
        errors=errors
    )

@router.post("/admin/frames/import", response_model=BulkImportResponse)
async def import_frames_from_json(
    json_data: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Bulk import frames from a JSON file or import a single frame.
    Accepts either a single frame object or an object with a "frames" array.
    """
    # Check admin access
    verify_admin(current_user, db)
    
    # Check if this is a single frame or a collection
    if "frames" in json_data:
        frames = json_data.get("frames", [])
    elif "id" in json_data and "name" in json_data and "image_url" in json_data:
        # This is a single frame
        frames = [json_data]
    else:
        # No valid frame data found
        frames = []
    
    if not frames:
        return BulkImportResponse(
            status="error",
            message="No frames found in the JSON data",
            imported_count=0
        )
    
    imported = 0
    errors = []
    
    for frame_data in frames:
        try:
            # Generate a unique ID if not provided
            frame_id = frame_data.get("id", str(uuid.uuid4()))
            
            # Check if this frame already exists
            existing = db.query(Frame).filter(Frame.id == frame_id).first()
            if existing:
                # Update existing frame
                for key, value in frame_data.items():
                    if key != "id" and hasattr(existing, key):
                        setattr(existing, key, value)
            else:
                # Create new frame
                new_frame = Frame(
                    id=frame_id,
                    name=frame_data.get("name", "Unnamed Frame"),
                    description=frame_data.get("description"),
                    image_url=frame_data.get("image_url", ""),
                    price_gems=frame_data.get("price_gems"),
                    price_usd=frame_data.get("price_usd"),
                    is_premium=frame_data.get("is_premium", False),
                    created_at=datetime.utcnow()
                )
                db.add(new_frame)
            
            imported += 1
        except Exception as e:
            errors.append(f"Error importing frame {frame_data.get('name', 'unknown')}: {str(e)}")
    
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        return BulkImportResponse(
            status="error",
            message=f"Database error: {str(e)}",
            imported_count=0,
            errors=[str(e)]
        )
    
    return BulkImportResponse(
        status="success",
        message=f"Successfully imported {imported} frames",
        imported_count=imported,
        errors=errors
    )

# Admin endpoint to get detailed information about avatars usage
@router.get("/admin/avatars/stats", response_model=Dict[str, Any])
async def get_avatar_stats(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Admin endpoint to get statistics about avatars usage
    """
    # Check admin access
    verify_admin(current_user, db)
    
    total_avatars = db.query(Avatar).count()
    default_avatars = db.query(Avatar).filter(Avatar.is_default == True).count()
    premium_avatars = db.query(Avatar).filter(Avatar.is_premium == True).count()
    
    # Count avatars by price range
    free_avatars = db.query(Avatar).filter(
        Avatar.price_gems.is_(None), 
        Avatar.price_usd.is_(None)
    ).count()
    
    gem_purchasable = db.query(Avatar).filter(
        Avatar.price_gems.isnot(None)
    ).count()
    
    usd_purchasable = db.query(Avatar).filter(
        Avatar.price_usd.isnot(None)
    ).count()
    
    # Get top 5 most popular avatars
    top_avatars = db.query(
        Avatar.id,
        Avatar.name,
        db.func.count(UserAvatar.avatar_id).label('purchase_count')
    ).join(
        UserAvatar, UserAvatar.avatar_id == Avatar.id
    ).group_by(
        Avatar.id, Avatar.name
    ).order_by(
        db.desc('purchase_count')
    ).limit(5).all()
    
    top_avatars_data = [
        {"id": avatar.id, "name": avatar.name, "purchase_count": avatar.purchase_count}
        for avatar in top_avatars
    ]
    
    return {
        "total_avatars": total_avatars,
        "default_avatars": default_avatars,
        "premium_avatars": premium_avatars,
        "free_avatars": free_avatars,
        "gem_purchasable": gem_purchasable,
        "usd_purchasable": usd_purchasable,
        "top_avatars": top_avatars_data
    }