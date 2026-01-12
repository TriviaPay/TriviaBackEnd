import os
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    HTTPException,
    Path,
    Query,
    UploadFile,
)
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_admin_user, get_current_user, verify_admin

from .schemas import (
    AdminStatusResponse,
    AvatarCreate,
    AvatarResponse,
    BadgeCreate,
    BadgeResponse,
    BadgeUpdate,
    BulkImportResponse,
    CreateSubscriptionPlanRequest,
    CreateSubscriptionRequest,
    FrameCreate,
    FrameResponse,
    GemPackageRequest,
    GemPackageResponse,
    AppVersionResponse,
    SubscriptionPlanResponse,
    UpdateAdminStatusRequest,
    UserAdminStatus,
)
from .service import (
    allocate_bronze_mode_questions_manual as admin_allocate_bronze_mode_questions_manual,
)
from .service import (
    allocate_free_mode_questions_manual as admin_allocate_free_mode_questions_manual,
)
from .service import check_subscription_status as admin_check_subscription_status
from .service import create_avatar as admin_create_avatar
from .service import create_badge as admin_create_badge
from .service import create_frame as admin_create_frame
from .service import create_gem_package as admin_create_gem_package
from .service import create_or_update_trivia_mode as admin_create_or_update_trivia_mode
from .service import create_subscription_for_user as admin_create_subscription_for_user
from .service import create_subscription_plan as admin_create_subscription_plan
from .service import delete_avatar as admin_delete_avatar
from .service import delete_frame as admin_delete_frame
from .service import delete_gem_package as admin_delete_gem_package
from .service import get_avatar_stats as admin_get_avatar_stats
from .service import get_badge_assignments as admin_get_badge_assignments
from .service import import_avatars_from_json as admin_import_avatars_from_json
from .service import import_frames_from_json as admin_import_frames_from_json
from .service import list_app_versions as admin_list_app_versions
from .service import list_subscription_plans as admin_list_subscription_plans
from .service import list_trivia_modes as admin_list_trivia_modes
from .service import list_users as auth_list_users
from .service import search_users as auth_search_users
from .service import trigger_bronze_mode_draw as admin_trigger_bronze_mode_draw
from .service import trigger_free_mode_draw as admin_trigger_free_mode_draw
from .service import update_avatar as admin_update_avatar
from .service import update_badge as admin_update_badge
from .service import update_frame as admin_update_frame
from .service import update_gem_package as admin_update_gem_package
from .service import update_user_admin_status as auth_update_user_admin_status
from .service import upload_questions_csv as admin_upload_questions_csv

router = APIRouter(prefix="/admin", tags=["Admin"])
MAX_QUESTION_UPLOAD_BYTES = int(
    os.getenv("MAX_QUESTION_UPLOAD_BYTES", str(5 * 1024 * 1024))
)


@router.get("/users", response_model=List[UserAdminStatus])
async def get_admin_users(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=500, description="Max records to return"),
):
    """
    Get all users with their admin status (admin-only endpoint)
    """
    # Verify admin access
    verify_admin(db, current_user)

    # Get all users with their admin status
    return auth_list_users(db, skip, limit)


@router.put("/users/{account_id}", response_model=AdminStatusResponse)
async def update_user_admin_status(
    account_id: int = Path(..., description="The account ID of the user to update"),
    admin_status: UpdateAdminStatusRequest = Body(
        ..., description="Updated admin status"
    ),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Update a user's admin status (admin-only endpoint)
    """
    # Verify admin access
    verify_admin(db, current_user)

    return auth_update_user_admin_status(db, account_id, admin_status.is_admin)


@router.get("/users/search", response_model=List[UserAdminStatus])
async def search_users(
    email: Optional[str] = Query(None, description="Email to search for"),
    username: Optional[str] = Query(None, description="Username to search for"),
    contains: bool = Query(False, description="Use substring search (slower)"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=500, description="Max records to return"),
):
    """
    Search for users by email or username (admin-only endpoint)
    """
    # Verify admin access
    verify_admin(db, current_user)

    if not email and not username:
        raise HTTPException(
            status_code=400, detail="Provide email or username to search"
        )
    return auth_search_users(db, email, username, None, contains, skip, limit)


@router.get("/app-versions", response_model=List[AppVersionResponse])
async def list_app_versions(
    user_id: Optional[int] = Query(None, description="User account ID to filter"),
    device_uuid: Optional[str] = Query(None, description="Device UUID to filter"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=500, description="Max records to return"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    List latest app versions per user/device (admin-only endpoint).
    """
    verify_admin(db, current_user)
    return admin_list_app_versions(db, user_id, device_uuid, skip, limit)


@router.post("/trivia/upload-questions")
async def upload_questions_csv(
    mode_id: str = Query(..., description="Mode ID (e.g., 'free_mode')"),
    file: UploadFile = File(..., description="CSV file with questions"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Upload CSV file with questions for a specific mode.
    CSV should have columns: question, option_a, option_b, option_c, option_d, correct_answer,
    fill_in_answer, hint, explanation, category, country, difficulty_level, picture_url
    """
    verify_admin(db, current_user)
    file_content = await file.read(MAX_QUESTION_UPLOAD_BYTES + 1)
    return await admin_upload_questions_csv(
        db, mode_id, file_content, MAX_QUESTION_UPLOAD_BYTES
    )


@router.get("/trivia/modes")
async def list_trivia_modes(
    db: Session = Depends(get_db), current_user: dict = Depends(get_current_user)
):
    """
    List all trivia modes.
    """
    verify_admin(db, current_user)
    return admin_list_trivia_modes(db)


@router.post("/trivia/modes")
async def create_or_update_mode(
    mode_id: str = Body(..., description="Mode ID"),
    mode_name: str = Body(..., description="Mode display name"),
    questions_count: int = Body(..., description="Number of questions per day"),
    reward_distribution: dict = Body(
        ..., description="Reward distribution config (JSON)"
    ),
    amount: float = Body(0.0, description="Entry fee amount"),
    leaderboard_types: list = Body(
        ..., description="Leaderboard types (e.g., ['daily'])"
    ),
    ad_config: Optional[dict] = Body(None, description="Ad configuration (JSON)"),
    survey_config: Optional[dict] = Body(
        None, description="Survey configuration (JSON)"
    ),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Create or update a trivia mode configuration.
    """
    verify_admin(db, current_user)
    return admin_create_or_update_trivia_mode(
        db,
        mode_id,
        mode_name,
        questions_count,
        reward_distribution,
        amount,
        leaderboard_types,
        ad_config,
        survey_config,
    )


@router.post("/trivia/free-mode/trigger-draw")
async def trigger_free_mode_draw(
    draw_date: Optional[str] = Body(
        None, description="Draw date (YYYY-MM-DD). Defaults to yesterday."
    ),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Manually trigger draw for free mode.
    Calculates winners, distributes gems, and cleans up old leaderboard.
    """
    verify_admin(db, current_user)
    return await admin_trigger_free_mode_draw(db, draw_date)


@router.post("/trivia/free-mode/allocate-questions")
async def allocate_free_mode_questions_manual(
    target_date: Optional[str] = Body(
        None, description="Target date (YYYY-MM-DD). Defaults to active draw date."
    ),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Manually trigger question allocation for free mode.
    Allocates questions from trivia_questions_free_mode to trivia_questions_free_mode_daily.
    """
    verify_admin(db, current_user)
    return await admin_allocate_free_mode_questions_manual(db, target_date)


@router.post("/trivia/bronze-mode/trigger-draw")
async def trigger_bronze_mode_draw(
    draw_date: Optional[str] = Body(
        None, description="Draw date (YYYY-MM-DD). Defaults to yesterday."
    ),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Manually trigger draw for $5 mode.
    Calculates winners, distributes money, and cleans up old leaderboard.
    """
    verify_admin(db, current_user)
    return await admin_trigger_bronze_mode_draw(db, draw_date)


@router.post("/trivia/bronze-mode/allocate-questions")
async def allocate_bronze_mode_questions_manual(
    target_date: Optional[str] = Body(
        None, description="Target date (YYYY-MM-DD). Defaults to active draw date."
    ),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Manually trigger question allocation for bronze mode.
    Allocates one question from trivia_questions_bronze_mode to trivia_questions_bronze_mode_daily.
    """
    verify_admin(db, current_user)
    return await admin_allocate_bronze_mode_questions_manual(db, target_date)


@router.get("/subscriptions/check")
async def check_subscription_status(
    plan_id: Optional[int] = Query(
        None,
        description="Subscription plan ID to check. If not provided, checks all plans.",
    ),
    price_usd: Optional[float] = Query(
        None, description="Filter by price in USD (e.g., 5.0 for $5 plans)"
    ),
    user_id: Optional[int] = Query(
        None, description="User account ID to check. If not provided, checks all users."
    ),
    plan_skip: int = Query(0, ge=0, description="Plans to skip"),
    plan_limit: int = Query(100, ge=1, le=500, description="Max plans to return"),
    sub_skip: int = Query(0, ge=0, description="Subscriptions to skip"),
    sub_limit: int = Query(
        100, ge=1, le=1000, description="Max subscriptions to return"
    ),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Check subscription plan and user subscription status.
    Generic endpoint that can check any subscription plan by plan_id or price.
    """
    verify_admin(db, current_user)
    return admin_check_subscription_status(
        db,
        plan_id,
        price_usd,
        user_id,
        plan_skip,
        plan_limit,
        sub_skip,
        sub_limit,
    )


@router.get("/subscriptions/plans", response_model=List[SubscriptionPlanResponse])
async def get_subscription_plans(
    livemode: Optional[bool] = Query(
        None, description="Filter plans by livemode (true=production, false=test)"
    ),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    List all subscription plans (admin-only). Optionally limit to livemode/test plans.
    """
    verify_admin(db, current_user)
    return admin_list_subscription_plans(db, livemode)


@router.post("/subscriptions/create-plan")
async def create_subscription_plan(
    request: CreateSubscriptionPlanRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Create a subscription plan if it doesn't exist.
    Generic endpoint that can create any subscription plan with specified price and interval.
    """
    verify_admin(db, current_user)
    return admin_create_subscription_plan(db, request)


@router.post("/subscriptions/create-subscription")
async def create_subscription_for_user(
    request: CreateSubscriptionRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Create an active subscription for a user (for testing/admin purposes).
    This creates a UserSubscription record linking the user to a plan.
    If user_id is not provided, creates subscription for the current user.
    Subscription duration is fixed at 30 days.
    """
    verify_admin(db, current_user)
    return admin_create_subscription_for_user(db, request, current_user)


# ======== Store Admin Endpoints ========


@router.post("/gem-packages", response_model=GemPackageResponse)
async def create_gem_package(
    package: GemPackageRequest = Body(..., description="Gem package details"),
    claims: dict = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin endpoint to create a new gem package"""
    payload = admin_create_gem_package(db, package)
    return GemPackageResponse(**payload)


@router.put("/gem-packages/{package_id}", response_model=GemPackageResponse)
async def update_gem_package(
    package_id: int = Path(..., description="ID of the gem package to update"),
    package: GemPackageRequest = Body(..., description="Updated gem package details"),
    claims: dict = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin endpoint to update an existing gem package"""
    payload = admin_update_gem_package(db, package_id, package)
    return GemPackageResponse(**payload)


@router.delete("/gem-packages/{package_id}", response_model=Dict[str, Any])
async def delete_gem_package(
    package_id: int = Path(..., description="ID of the gem package to delete"),
    claims: dict = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Admin endpoint to delete a gem package"""
    return admin_delete_gem_package(db, package_id)


# ======== Badges Admin Endpoints ========


@router.post("/badges", response_model=BadgeResponse)
async def create_badge(
    badge: BadgeCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user),
):
    """Admin endpoint to create a new badge (stored in TriviaModeConfig)."""
    payload = admin_create_badge(db, badge)
    return BadgeResponse(**payload)


@router.put("/badges/{badge_id}", response_model=BadgeResponse)
async def update_badge(
    badge_id: str = Path(..., description="The ID of the badge to update (mode_id)"),
    badge_update: BadgeUpdate = Body(..., description="Updated badge data"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user),
):
    """Admin endpoint to update an existing badge (stored in TriviaModeConfig)."""
    payload = admin_update_badge(db, badge_id, badge_update)
    return BadgeResponse(**payload)


@router.get("/badges/assignments", response_model=Dict[str, Any])
async def get_badge_assignments(
    db: Session = Depends(get_db), current_user: dict = Depends(get_admin_user)
):
    """Admin endpoint to get badge assignment statistics"""
    return admin_get_badge_assignments(db)


# ======== Cosmetics Admin Endpoints ========


@router.post("/avatars", response_model=AvatarResponse)
async def create_avatar(
    avatar: AvatarCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user),
):
    """Admin endpoint to create a new avatar"""
    return admin_create_avatar(db, avatar)


@router.put("/avatars/{avatar_id}", response_model=AvatarResponse)
async def update_avatar(
    avatar_id: str = Path(..., description="The ID of the avatar to update"),
    avatar_update: AvatarCreate = Body(..., description="Updated avatar data"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user),
):
    """Admin endpoint to update an existing avatar"""
    return admin_update_avatar(db, avatar_id, avatar_update)


@router.delete("/avatars/{avatar_id}", response_model=dict)
async def delete_avatar(
    avatar_id: str = Path(..., description="The ID of the avatar to delete"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user),
):
    """Admin endpoint to delete an avatar"""
    return admin_delete_avatar(db, avatar_id)


@router.post("/frames", response_model=FrameResponse)
async def create_frame(
    frame: FrameCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user),
):
    """Admin endpoint to create a new frame"""
    return admin_create_frame(db, frame)


@router.put("/frames/{frame_id}", response_model=FrameResponse)
async def update_frame(
    frame_id: str = Path(..., description="The ID of the frame to update"),
    frame_update: FrameCreate = Body(..., description="Updated frame data"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user),
):
    """Admin endpoint to update an existing frame"""
    return admin_update_frame(db, frame_id, frame_update)


@router.delete("/frames/{frame_id}", response_model=dict)
async def delete_frame(
    frame_id: str = Path(..., description="The ID of the frame to delete"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user),
):
    """Admin endpoint to delete a frame"""
    return admin_delete_frame(db, frame_id)


@router.post("/avatars/import", response_model=BulkImportResponse)
async def import_avatars_from_json(
    json_data: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user),
):
    """Bulk import avatars from a JSON file or import a single avatar."""
    payload = admin_import_avatars_from_json(db, json_data)
    return BulkImportResponse(**payload)


@router.post("/frames/import", response_model=BulkImportResponse)
async def import_frames_from_json(
    json_data: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user),
):
    """Bulk import frames from a JSON file or import a single frame."""
    payload = admin_import_frames_from_json(db, json_data)
    return BulkImportResponse(**payload)


@router.get("/avatars/stats", response_model=Dict[str, Any])
async def get_avatar_stats(
    db: Session = Depends(get_db), current_user: dict = Depends(get_admin_user)
):
    """Admin endpoint to get statistics about avatars usage"""
    return admin_get_avatar_stats(db)
