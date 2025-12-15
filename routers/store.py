from fastapi import APIRouter, Depends, HTTPException, status, Path, Body
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from datetime import datetime

from db import get_db
from models import User, GemPackageConfig, UserGemPurchase
from routers.dependencies import get_current_user
from utils.storage import presign_get
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/store", tags=["Store"])

class PurchaseResponse(BaseModel):
    """Model for purchase responses"""
    success: bool
    remaining_gems: Optional[int] = None
    remaining_balance: Optional[float] = None
    message: str

class BuyGemsRequest(BaseModel):
    """Model for buying gems with wallet balance"""
    package_id: int = Field(
        ...,
        description="ID of the gem package to purchase"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "package_id": 1
            }
        }

class GemPackageResponse(BaseModel):
    """Model for gem package response"""
    id: int
    price_usd: float
    gems_amount: int
    is_one_time: bool
    description: Optional[str]
    url: Optional[str] = None  # Presigned S3 URL
    mime_type: Optional[str] = None  # MIME type of the image
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

@router.post("/buy-gems", response_model=PurchaseResponse)
async def buy_gems_with_wallet(
    request: BuyGemsRequest = Body(..., description="Gem purchase details"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Buy gems using wallet balance"""
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get gem package from database
    gem_package = db.query(GemPackageConfig).filter(GemPackageConfig.id == request.package_id).first()
    if not gem_package:
        raise HTTPException(status_code=404, detail=f"Gem package with ID {request.package_id} not found")
    
    # Get price in minor units
    price_minor = gem_package.price_minor if gem_package.price_minor is not None else 0
    price_usd_display = price_minor / 100.0
    
    # Check wallet balance (use wallet_balance_minor if available)
    wallet_balance_minor = user.wallet_balance_minor if user.wallet_balance_minor is not None else int((user.wallet_balance or 0) * 100)
    
    if wallet_balance_minor < price_minor:
        raise HTTPException(
            status_code=400, 
            detail=f"Insufficient wallet balance. You have ${wallet_balance_minor / 100.0:.2f}, but this package costs ${price_usd_display:.2f}"
        )
    
    # Check if this is a one-time offer that the user has already purchased
    if gem_package.is_one_time:
        # Check if THIS user has already purchased this one-time package
        existing_purchase = db.query(UserGemPurchase).filter(
            UserGemPurchase.user_id == user.account_id,
            UserGemPurchase.package_id == gem_package.id
        ).first()
        
        if existing_purchase:
            raise HTTPException(
                status_code=400,
                detail=f"You have already purchased this one-time offer on {existing_purchase.purchase_date}"
            )
    
    # Deduct from wallet and add gems
    # Update wallet_balance_minor if available, otherwise use wallet_balance
    if user.wallet_balance_minor is not None:
        user.wallet_balance_minor -= price_minor
        user.wallet_balance = user.wallet_balance_minor / 100.0  # Keep in sync
    else:
        user.wallet_balance = (wallet_balance_minor - price_minor) / 100.0
    
    user.gems += gem_package.gems_amount
    user.last_wallet_update = datetime.utcnow()
    
    # Record the purchase in the user_gem_purchases table
    purchase_record = UserGemPurchase(
        user_id=user.account_id,
        package_id=gem_package.id,
        price_paid=price_usd_display,
        gems_received=gem_package.gems_amount
    )
    db.add(purchase_record)
    
    db.commit()
    
    remaining_balance = user.wallet_balance_minor / 100.0 if user.wallet_balance_minor is not None else user.wallet_balance
    
    return PurchaseResponse(
        success=True,
        remaining_gems=user.gems,
        remaining_balance=remaining_balance,
        message=f"Successfully purchased {gem_package.gems_amount} gems for ${price_usd_display:.2f}"
    )

@router.get("/gem-packages", response_model=List[GemPackageResponse])
async def get_gem_packages(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all available gem packages with presigned URLs for images"""
    packages = db.query(GemPackageConfig).all()
    result = []
    for pkg in packages:
        # Generate presigned URL if bucket and object_key are present
        signed_url = None
        if pkg.bucket and pkg.object_key:
            try:
                signed_url = presign_get(pkg.bucket, pkg.object_key, expires=900)  # 15 minutes
                if not signed_url:
                    logging.warning(f"presign_get returned None for gem package {pkg.id} with bucket={pkg.bucket}, key={pkg.object_key}")
            except Exception as e:
                logging.error(f"Failed to presign gem package {pkg.id}: {e}", exc_info=True)
        else:
            logging.debug(f"Gem package {pkg.id} missing bucket/object_key: bucket={pkg.bucket}, object_key={pkg.object_key}")
        
        # Use price_minor if available, otherwise fall back to price_usd
        price_minor = pkg.price_minor if pkg.price_minor is not None else 0
        price_usd_display = price_minor / 100.0 if price_minor else 0.0
        
        result.append(GemPackageResponse(
            id=pkg.id,
            price_usd=price_usd_display,
            gems_amount=pkg.gems_amount,
            is_one_time=pkg.is_one_time,
            description=pkg.description,
            url=signed_url,
            mime_type=pkg.mime_type,
            created_at=pkg.created_at,
            updated_at=pkg.updated_at
        ))
    return result

