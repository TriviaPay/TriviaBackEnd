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
from utils.storage import presign_get

# Load environment variables
load_dotenv()

router = APIRouter(prefix="/cosmetics", tags=["Cosmetics"])

# ======== Helper Functions ========

# Removed is_admin and verify_admin functions

# ======== Pydantic Models for Request/Response Validation ========

class CosmeticBase(BaseModel):
    name: str
    description: Optional[str] = None
    price_gems: Optional[int] = None
    price_minor: Optional[int] = None  # Price in minor units (cents)
    is_premium: bool = False
    bucket: Optional[str] = None  # S3 bucket name
    object_key: Optional[str] = None  # S3 object key
    mime_type: Optional[str] = None  # MIME type (e.g., image/png, application/json)

class AvatarCreate(CosmeticBase):
    """Schema for creating a new avatar"""
    id: Optional[str] = None

class AvatarResponse(CosmeticBase):
    """Schema for avatar response"""
    id: str
    created_at: datetime
    price_usd: Optional[float] = None
    url: Optional[str] = None
    mime_type: Optional[str] = None
    
    class Config:
        from_attributes = True

class FrameCreate(CosmeticBase):
    """Schema for creating a new frame"""
    id: Optional[str] = None

class FrameResponse(CosmeticBase):
    """Schema for frame response"""
    id: str
    created_at: datetime
    price_usd: Optional[float] = None
    url: Optional[str] = None
    mime_type: Optional[str] = None
    
    class Config:
        from_attributes = True

class UserCosmeticResponse(BaseModel):
    """Schema for user-owned cosmetics response"""
    id: str
    name: str
    description: Optional[str]
    is_premium: bool
    purchase_date: datetime
    url: Optional[str] = None  # Presigned URL (primary)
    mime_type: Optional[str] = None
    
    class Config:
        from_attributes = True

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
def get_all_avatars(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = 0,
    limit: int = 100,
    include_urls: bool = Query(True, description="Include presigned URLs")
):
    """
    Get all available avatars, ordered by most recently added first
    """
    avatars = db.query(Avatar).order_by(desc(Avatar.created_at)).offset(skip).limit(limit).all()
    out: List[AvatarResponse] = []
    presign_cache: Dict[tuple[str, str], Optional[str]] = {}
    for av in avatars:
        signed = None
        bucket = getattr(av, "bucket", None)
        object_key = getattr(av, "object_key", None)
        if include_urls and bucket and object_key:
            try:
                cache_key = (bucket, object_key)
                if cache_key in presign_cache:
                    signed = presign_cache[cache_key]
                else:
                    signed = presign_get(bucket, object_key, expires=900)
                    presign_cache[cache_key] = signed
                if not signed:
                    logging.warning(f"presign_get returned None for avatar {av.id} with bucket={bucket}, key={object_key}")
            except Exception as e:
                logging.error(f"Failed to presign avatar {av.id}: {e}", exc_info=True)
        else:
            logging.debug(f"Avatar {av.id} missing bucket/object_key: bucket={bucket}, object_key={object_key}")
        out.append(AvatarResponse(
            id=av.id,
            name=av.name,
            description=av.description,
            price_gems=av.price_gems,
            price_usd=av.price_usd,
            is_premium=av.is_premium,
            created_at=av.created_at,
            url=signed,
            mime_type=getattr(av, "mime_type", None)
        ))
    return out

@router.get("/avatars/owned", response_model=List[UserCosmeticResponse])
def get_user_avatars(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    include_urls: bool = Query(True, description="Include presigned URLs")
):
    """
    Get all avatars owned by the current user
    """
    user = current_user
    rows = db.query(Avatar, UserAvatar.purchase_date).join(
        UserAvatar, UserAvatar.avatar_id == Avatar.id
    ).filter(UserAvatar.user_id == user.account_id).order_by(desc(UserAvatar.purchase_date)).all()
    out: List[UserCosmeticResponse] = []
    presign_cache: Dict[tuple[str, str], Optional[str]] = {}
    for av, purchased_at in rows:
        signed = None
        bucket = getattr(av, "bucket", None)
        object_key = getattr(av, "object_key", None)
        if include_urls and bucket and object_key:
            try:
                cache_key = (bucket, object_key)
                if cache_key in presign_cache:
                    signed = presign_cache[cache_key]
                else:
                    signed = presign_get(bucket, object_key, expires=900)
                    presign_cache[cache_key] = signed
                if not signed:
                    logging.warning(f"presign_get returned None for avatar {av.id} with bucket={bucket}, key={object_key}")
            except Exception as e:
                logging.error(f"Failed to presign avatar {av.id}: {e}", exc_info=True)
        else:
            logging.debug(f"Avatar {av.id} missing bucket/object_key: bucket={bucket}, object_key={object_key}")
        out.append(UserCosmeticResponse(
            id=av.id,
            name=av.name,
            description=av.description,
            is_premium=av.is_premium,
            purchase_date=purchased_at,
            url=signed,
            mime_type=getattr(av, "mime_type", None)
        ))
    return out

@router.post("/avatars/buy/{avatar_id}", response_model=PurchaseResponse)
async def buy_avatar(
    avatar_id: str = Path(..., description="The ID of the avatar to purchase"),
    payment_method: str = Query(..., description="Payment method: 'gems' or 'usd'"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Purchase an avatar using gems or USD
    """
    user = current_user
    
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
        
        # Lock user row to prevent concurrent gem updates
        user = db.query(User).filter(
            User.account_id == current_user.account_id
        ).with_for_update().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Check if user has enough gems
        if user.gems < avatar.price_gems:
            raise HTTPException(
                status_code=400,
                detail=f"Not enough gems. You have {user.gems} gems, but this avatar costs {avatar.price_gems} gems"
            )
        
        # Deduct gems from user
        user.gems -= avatar.price_gems
        
        # Create ownership record with idempotency handling
        try:
            new_ownership = UserAvatar(
                user_id=user.account_id,
                avatar_id=avatar_id,
                purchase_date=datetime.utcnow()
            )
            
            db.add(new_ownership)
            db.commit()
            db.refresh(new_ownership)
            
            return PurchaseResponse(
                status="success",
                message=f"Successfully purchased avatar '{avatar.name}' for {avatar.price_gems} gems",
                item_id=avatar_id,
                purchase_date=new_ownership.purchase_date,
                gems_spent=avatar.price_gems
            )
        except IntegrityError:
            # Idempotent buy: ownership already exists (from unique constraint)
            db.rollback()
            # Get existing ownership
            existing = db.query(UserAvatar).filter(
                UserAvatar.user_id == user.account_id,
                UserAvatar.avatar_id == avatar_id
            ).first()
            return PurchaseResponse(
                status="success",
                message=f"You already own the avatar '{avatar.name}'",
                item_id=avatar_id,
                purchase_date=existing.purchase_date,
                gems_spent=0  # No gems spent for duplicate
            )
    
    elif payment_method == "usd":
        # Check if the avatar can be purchased with USD
        if avatar.price_minor is None or avatar.price_minor == 0:
            raise HTTPException(
                status_code=400,
                detail=f"Avatar '{avatar.name}' cannot be purchased with USD"
            )
        
        # TODO: Implement actual payment processing
        # For now, just create the ownership record with idempotency handling
        try:
            new_ownership = UserAvatar(
                user_id=user.account_id,
                avatar_id=avatar_id,
                purchase_date=datetime.utcnow()
            )
            
            db.add(new_ownership)
            db.commit()
            db.refresh(new_ownership)
            
            return PurchaseResponse(
                status="success",
                message=f"Successfully purchased avatar '{avatar.name}' for ${avatar.price_usd}",
                item_id=avatar_id,
                purchase_date=new_ownership.purchase_date,
                usd_spent=avatar.price_usd  # Use computed property
            )
        except IntegrityError:
            # Idempotent buy: ownership already exists (from unique constraint)
            db.rollback()
            # Get existing ownership
            existing = db.query(UserAvatar).filter(
                UserAvatar.user_id == user.account_id,
                UserAvatar.avatar_id == avatar_id
            ).first()
            return PurchaseResponse(
                status="success",
                message=f"You already own the avatar '{avatar.name}'",
                item_id=avatar_id,
                purchase_date=existing.purchase_date,
                usd_spent=0  # No USD spent for duplicate
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
    current_user: User = Depends(get_current_user)
):
    """
    Select an avatar as the current profile avatar.
    This will clear any custom profile picture (only one can be active at a time).
    """
    user = current_user
    
    # Check if user owns this avatar or if it's a default avatar
    avatar = db.query(Avatar).filter(Avatar.id == avatar_id).first()
    if not avatar:
        raise HTTPException(status_code=404, detail=f"Avatar with ID {avatar_id} not found")
    
    ownership = db.query(UserAvatar).filter(
        UserAvatar.user_id == user.account_id,
        UserAvatar.avatar_id == avatar_id
    ).first()
    
    if not ownership:
        raise HTTPException(
            status_code=403,
            detail=f"You don't own the avatar with ID {avatar_id}"
        )
    
    # Clear custom profile picture (only one can be active at a time)
    user.profile_pic_url = None
    
    # Update the user's selected avatar
    user.selected_avatar_id = avatar_id
    db.commit()
    
    return SelectResponse(
        status="success",
        message=f"Successfully selected avatar '{avatar.name}' as your profile avatar",
        selected_id=avatar_id
    )

# Admin endpoints moved to admin.py router

# ======== Frame Endpoints ========

@router.get("/frames", response_model=List[FrameResponse])
def get_all_frames(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = 0,
    limit: int = 100,
    include_urls: bool = Query(True, description="Include presigned URLs")
):
    """
    Get all available frames, ordered by most recently added first
    """
    frames = db.query(Frame).order_by(desc(Frame.created_at)).offset(skip).limit(limit).all()
    out: List[FrameResponse] = []
    presign_cache: Dict[tuple[str, str], Optional[str]] = {}
    for fr in frames:
        signed = None
        if include_urls and getattr(fr, "bucket", None) and getattr(fr, "object_key", None):
            try:
                cache_key = (fr.bucket, fr.object_key)
                if cache_key in presign_cache:
                    signed = presign_cache[cache_key]
                else:
                    signed = presign_get(fr.bucket, fr.object_key, expires=900)
                    presign_cache[cache_key] = signed
            except Exception as e:
                logging.warning(f"Failed to presign frame {fr.id}: {e}")
        out.append(FrameResponse(
            id=fr.id,
            name=fr.name,
            description=fr.description,
            price_gems=fr.price_gems,
            price_usd=fr.price_usd,
            is_premium=fr.is_premium,
            created_at=fr.created_at,
            url=signed,
            mime_type=getattr(fr, "mime_type", None)
        ))
    return out

@router.get("/frames/owned", response_model=List[UserCosmeticResponse])
def get_user_frames(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    include_urls: bool = Query(True, description="Include presigned URLs")
):
    """
    Get all frames owned by the current user
    """
    user = current_user
    rows = db.query(Frame, UserFrame.purchase_date).join(
        UserFrame, UserFrame.frame_id == Frame.id
    ).filter(UserFrame.user_id == user.account_id).order_by(desc(UserFrame.purchase_date)).all()
    out: List[UserCosmeticResponse] = []
    presign_cache: Dict[tuple[str, str], Optional[str]] = {}
    for fr, purchased_at in rows:
        signed = None
        if include_urls and getattr(fr, "bucket", None) and getattr(fr, "object_key", None):
            try:
                cache_key = (fr.bucket, fr.object_key)
                if cache_key in presign_cache:
                    signed = presign_cache[cache_key]
                else:
                    signed = presign_get(fr.bucket, fr.object_key, expires=900)
                    presign_cache[cache_key] = signed
            except Exception as e:
                logging.warning(f"Failed to presign frame {fr.id}: {e}")
        out.append(UserCosmeticResponse(
            id=fr.id,
            name=fr.name,
            description=fr.description,
            is_premium=fr.is_premium,
            purchase_date=purchased_at,
            url=signed,
            mime_type=getattr(fr, "mime_type", None)
        ))
    return out

@router.post("/frames/buy/{frame_id}", response_model=PurchaseResponse)
async def buy_frame(
    frame_id: str = Path(..., description="The ID of the frame to purchase"),
    payment_method: str = Query(..., description="Payment method: 'gems' or 'usd'"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Purchase a frame using gems or USD
    """
    user = current_user
    
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
        
        # Lock user row to prevent concurrent gem updates
        user = db.query(User).filter(
            User.account_id == current_user.account_id
        ).with_for_update().first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Check if user has enough gems
        if user.gems < frame.price_gems:
            raise HTTPException(
                status_code=400,
                detail=f"Not enough gems. You have {user.gems} gems, but this frame costs {frame.price_gems} gems"
            )
        
        # Deduct gems from user
        user.gems -= frame.price_gems
        
        # Create ownership record with idempotency handling
        try:
            new_ownership = UserFrame(
                user_id=user.account_id,
                frame_id=frame_id,
                purchase_date=datetime.utcnow()
            )
            
            db.add(new_ownership)
            db.commit()
            db.refresh(new_ownership)
            
            return PurchaseResponse(
                status="success",
                message=f"Successfully purchased frame '{frame.name}' for {frame.price_gems} gems",
                item_id=frame_id,
                purchase_date=new_ownership.purchase_date,
                gems_spent=frame.price_gems
            )
        except IntegrityError:
            # Idempotent buy: ownership already exists (from unique constraint)
            db.rollback()
            # Get existing ownership
            existing = db.query(UserFrame).filter(
                UserFrame.user_id == user.account_id,
                UserFrame.frame_id == frame_id
            ).first()
            return PurchaseResponse(
                status="success",
                message=f"You already own the frame '{frame.name}'",
                item_id=frame_id,
                purchase_date=existing.purchase_date,
                gems_spent=0  # No gems spent for duplicate
            )
    
    elif payment_method == "usd":
        # Check if the frame can be purchased with USD
        if frame.price_minor is None or frame.price_minor == 0:
            raise HTTPException(
                status_code=400,
                detail=f"Frame '{frame.name}' cannot be purchased with USD"
            )
        
        # TODO: Implement actual payment processing
        # For now, just create the ownership record with idempotency handling
        try:
            new_ownership = UserFrame(
                user_id=user.account_id,
                frame_id=frame_id,
                purchase_date=datetime.utcnow()
            )
            
            db.add(new_ownership)
            db.commit()
            db.refresh(new_ownership)
            
            return PurchaseResponse(
                status="success",
                message=f"Successfully purchased frame '{frame.name}' for ${frame.price_usd}",
                item_id=frame_id,
                purchase_date=new_ownership.purchase_date,
                usd_spent=frame.price_usd
            )
        except IntegrityError:
            # Idempotent buy: ownership already exists (from unique constraint)
            db.rollback()
            # Get existing ownership
            existing = db.query(UserFrame).filter(
                UserFrame.user_id == user.account_id,
                UserFrame.frame_id == frame_id
            ).first()
            return PurchaseResponse(
                status="success",
                message=f"You already own the frame '{frame.name}'",
                item_id=frame_id,
                purchase_date=existing.purchase_date,
                usd_spent=0  # No USD spent for duplicate
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
    current_user: User = Depends(get_current_user)
):
    """
    Select a frame as the current profile frame
    """
    user = current_user
    
    # Check if user owns this frame or if it's a default frame
    frame = db.query(Frame).filter(Frame.id == frame_id).first()
    if not frame:
        raise HTTPException(status_code=404, detail=f"Frame with ID {frame_id} not found")
    
    ownership = db.query(UserFrame).filter(
        UserFrame.user_id == user.account_id,
        UserFrame.frame_id == frame_id
    ).first()
    
    if not ownership:
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

# Admin endpoints moved to admin.py router

# Admin endpoints moved to admin.py router
