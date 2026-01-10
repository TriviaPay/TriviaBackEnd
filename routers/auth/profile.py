from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.orm import Session

from db import get_db
from models import User
from routers.dependencies import get_current_user

from .schemas import ExtendedProfileUpdate
from .service import (
    change_username,
    get_all_modes_status,
    get_complete_profile,
    get_profile_summary,
    get_user_gems,
    send_referral,
    update_extended_profile,
    upload_profile_picture,
)

router = APIRouter(prefix="/profile", tags=["Profile"])


@router.get("/gems", status_code=200)
async def get_user_gems_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return get_user_gems(db, current_user)


@router.post("/extended-update", status_code=200)
async def update_extended_profile_endpoint(
    request: Request,
    profile: ExtendedProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await update_extended_profile(request, profile, db, current_user)


@router.get("/complete", status_code=200)
async def get_complete_profile_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await get_complete_profile(db, current_user)


@router.post("/change-username")
def change_username_endpoint(
    new_username: str, user=Depends(get_current_user), db=Depends(get_db)
):
    return change_username(new_username, user, db)


@router.get("/summary", status_code=200)
async def get_profile_summary_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await get_profile_summary(db, current_user)


@router.post("/send-referral", status_code=200)
async def send_referral_endpoint(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await send_referral(db, current_user)


@router.post("/upload-profile-pic", status_code=200)
async def upload_profile_picture_endpoint(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await upload_profile_picture(file, db, current_user)


@router.get("/modes/status", status_code=200)
async def get_all_modes_status_endpoint(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_all_modes_status(user, db)
