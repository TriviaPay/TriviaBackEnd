from typing import List

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import (
    AvatarResponse,
    CosmeticPurchaseResponse,
    CosmeticSelectResponse,
    FrameResponse,
    UserCosmeticResponse,
)
from .service import (
    buy_avatar as service_buy_avatar,
    buy_frame as service_buy_frame,
    list_avatars as service_list_avatars,
    list_frames as service_list_frames,
    list_owned_avatars as service_list_owned_avatars,
    list_owned_frames as service_list_owned_frames,
    select_avatar as service_select_avatar,
    select_frame as service_select_frame,
)

router = APIRouter(prefix="/cosmetics", tags=["Cosmetics"])


@router.get("/avatars", response_model=List[AvatarResponse])
def get_all_avatars(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    skip: int = 0,
    limit: int = 100,
    include_urls: bool = Query(True, description="Include presigned URLs"),
):
    return service_list_avatars(
        db,
        current_user=current_user,
        skip=skip,
        limit=limit,
        include_urls=include_urls,
    )


@router.get("/avatars/owned", response_model=List[UserCosmeticResponse])
def get_user_avatars(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    include_urls: bool = Query(True, description="Include presigned URLs"),
):
    return service_list_owned_avatars(
        db, current_user=current_user, include_urls=include_urls
    )


@router.post("/avatars/buy/{avatar_id}", response_model=CosmeticPurchaseResponse)
async def buy_avatar(
    avatar_id: str = Path(..., description="The ID of the avatar to purchase"),
    payment_method: str = Query(..., description="Payment method: 'gems' or 'usd'"),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    return service_buy_avatar(
        db,
        current_user=current_user,
        avatar_id=avatar_id,
        payment_method=payment_method,
    )


@router.post("/avatars/select/{avatar_id}", response_model=CosmeticSelectResponse)
async def select_avatar(
    avatar_id: str = Path(..., description="The ID of the avatar to select"),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    return service_select_avatar(db, current_user=current_user, avatar_id=avatar_id)


@router.get("/frames", response_model=List[FrameResponse])
def get_all_frames(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    skip: int = 0,
    limit: int = 100,
    include_urls: bool = Query(True, description="Include presigned URLs"),
):
    return service_list_frames(
        db,
        current_user=current_user,
        skip=skip,
        limit=limit,
        include_urls=include_urls,
    )


@router.get("/frames/owned", response_model=List[UserCosmeticResponse])
def get_user_frames(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
    include_urls: bool = Query(True, description="Include presigned URLs"),
):
    return service_list_owned_frames(
        db, current_user=current_user, include_urls=include_urls
    )


@router.post("/frames/buy/{frame_id}", response_model=CosmeticPurchaseResponse)
async def buy_frame(
    frame_id: str = Path(..., description="The ID of the frame to purchase"),
    payment_method: str = Query(..., description="Payment method: 'gems' or 'usd'"),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    return service_buy_frame(
        db,
        current_user=current_user,
        frame_id=frame_id,
        payment_method=payment_method,
    )


@router.post("/frames/select/{frame_id}", response_model=CosmeticSelectResponse)
async def select_frame(
    frame_id: str = Path(..., description="The ID of the frame to select"),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    return service_select_frame(db, current_user=current_user, frame_id=frame_id)
