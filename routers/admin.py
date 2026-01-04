import os
from datetime import date, datetime, time
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Body, status, Path, Query
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
import pytz
import logging

from db import get_db
from models import (
    User, TriviaModeConfig, SubscriptionPlan, UserSubscription,
    GemPackageConfig, Avatar, Frame, UserAvatar, UserFrame
)
from routers.dependencies import get_admin_user, get_current_user, verify_admin
# Legacy perform_draw removed - use mode-specific draws instead
from utils.question_upload_service import parse_csv_questions, save_questions_to_mode
from utils.free_mode_rewards import (
    get_eligible_participants_free_mode, rank_participants_by_completion,
    calculate_reward_distribution, distribute_rewards_to_winners, cleanup_old_leaderboard
)
from utils.trivia_mode_service import get_mode_config
from utils.storage import presign_get
from fastapi import UploadFile, File
from datetime import datetime
import json
import uuid
import logging
import random

router = APIRouter(prefix="/admin", tags=["Admin"])
MAX_QUESTION_UPLOAD_BYTES = int(os.getenv("MAX_QUESTION_UPLOAD_BYTES", str(5 * 1024 * 1024)))


def _select_random_rows(base_query, count: int, order_col):
    total = base_query.count()
    if total <= 0:
        return []
    if total <= count:
        return base_query.order_by(order_col).all()

    offsets = random.sample(range(total), count)
    results = []
    seen_ids = set()
    for offset in offsets:
        row = base_query.order_by(order_col).offset(offset).limit(1).first()
        if row and row.id not in seen_ids:
            results.append(row)
            seen_ids.add(row.id)
    while len(results) < count:
        offset = random.randrange(total)
        row = base_query.order_by(order_col).offset(offset).limit(1).first()
        if row and row.id not in seen_ids:
            results.append(row)
            seen_ids.add(row.id)
    return results

# Request models
class DrawConfigUpdateRequest(BaseModel):
    is_custom: Optional[bool] = Field(None, description="Whether to use custom winner count")
    custom_winner_count: Optional[int] = Field(None, description="Custom number of winners when is_custom is True")
    draw_time_hour: Optional[int] = Field(None, ge=0, le=23, description="Hour of the day for the draw (0-23)")
    draw_time_minute: Optional[int] = Field(None, ge=0, le=59, description="Minute of the hour for the draw (0-59)")
    draw_timezone: Optional[str] = Field(None, description="Timezone for the draw (e.g., US/Eastern)")

# Response models
class DrawConfigResponse(BaseModel):
    is_custom: bool
    custom_winner_count: Optional[int] = None
    draw_time_hour: int
    draw_time_minute: int
    draw_timezone: str

class DrawResponse(BaseModel):
    status: str
    draw_date: date
    total_participants: int
    total_winners: int
    prize_pool: float
    winners: List[Dict[str, Any]]

class UserAdminStatus(BaseModel):
    account_id: int
    email: str
    username: Optional[str] = None
    is_admin: bool
    
    class Config:
        from_attributes = True

class UpdateAdminStatusRequest(BaseModel):
    is_admin: bool = Field(..., description="Admin status to set for the user")

class AdminStatusResponse(BaseModel):
    account_id: int
    email: str
    username: Optional[str] = None
    is_admin: bool
    message: str

# ======== Store Admin Models ========
class GemPackageRequest(BaseModel):
    price_minor: int = Field(..., description="Price in minor units (cents)")
    gems_amount: int = Field(..., description="Number of gems in the package")
    is_one_time: bool = Field(False, description="Whether this is a one-time offer")
    description: Optional[str] = Field(None, description="Description of the package")
    bucket: Optional[str] = Field(None, description="S3 bucket name for the package image")
    object_key: Optional[str] = Field(None, description="S3 object key for the package image")
    mime_type: Optional[str] = Field(None, description="MIME type of the image")

class GemPackageResponse(BaseModel):
    id: int
    price_usd: float
    gems_amount: int
    is_one_time: bool
    description: Optional[str]
    url: Optional[str] = None
    mime_type: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

# ======== Badges Admin Models ========
class BadgeBase(BaseModel):
    name: str
    description: Optional[str] = None
    image_url: str
    level: int

class BadgeCreate(BadgeBase):
    id: Optional[str] = None  # This will be mode_id in TriviaModeConfig

class BadgeUpdate(BadgeBase):
    pass

class BadgeResponse(BadgeBase):
    id: str  # This is mode_id
    created_at: datetime
    
    class Config:
        from_attributes = True

# ======== Cosmetics Admin Models ========
class CosmeticBase(BaseModel):
    name: str
    description: Optional[str] = None
    price_gems: Optional[int] = None
    price_minor: Optional[int] = None
    is_premium: bool = False
    bucket: Optional[str] = None
    object_key: Optional[str] = None
    mime_type: Optional[str] = None

class AvatarCreate(CosmeticBase):
    id: Optional[str] = None

class AvatarResponse(CosmeticBase):
    id: str
    created_at: datetime
    url: Optional[str] = None
    mime_type: Optional[str] = None
    
    class Config:
        from_attributes = True

class FrameCreate(CosmeticBase):
    id: Optional[str] = None

class FrameResponse(CosmeticBase):
    id: str
    created_at: datetime
    url: Optional[str] = None
    mime_type: Optional[str] = None
    
    class Config:
        from_attributes = True

class BulkImportResponse(BaseModel):
    status: str
    message: str
    imported_count: int
    errors: List[str] = []

# Legacy draw endpoints removed - use mode-specific draws instead:
# - /admin/trivia/free-mode/trigger-draw
# - /admin/trivia/bronze-mode/trigger-draw
# - /admin/trivia/silver-mode/trigger-draw

@router.get("/users", response_model=List[UserAdminStatus])
async def get_admin_users(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=500, description="Max records to return")
):
    """
    Get all users with their admin status (admin-only endpoint)
    """
    # Verify admin access
    verify_admin(current_user)
    
    # Get all users with their admin status
    users = db.query(User).order_by(User.account_id).offset(skip).limit(limit).all()
    return users

@router.put("/users/{account_id}", response_model=AdminStatusResponse)
async def update_user_admin_status(
    account_id: int = Path(..., description="The account ID of the user to update"),
    admin_status: UpdateAdminStatusRequest = Body(..., description="Updated admin status"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Update a user's admin status (admin-only endpoint)
    """
    # Verify admin access
    verify_admin(current_user)
    
    # Find the user
    user = db.query(User).filter(User.account_id == account_id).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User with account ID {account_id} not found")
    
    # Update admin status
    user.is_admin = admin_status.is_admin
    db.commit()
    db.refresh(user)
    
    # Generate appropriate message
    message = f"User {user.email} is now {'an admin' if user.is_admin else 'not an admin'}"
    
    return {
        "account_id": user.account_id,
        "email": user.email,
        "username": user.username,
        "is_admin": user.is_admin,
        "message": message
    }

@router.get("/users/search", response_model=List[UserAdminStatus])
async def search_users(
    email: Optional[str] = Query(None, description="Email to search for"),
    username: Optional[str] = Query(None, description="Username to search for"),
    contains: bool = Query(False, description="Use substring search (slower)"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=500, description="Max records to return")
):
    """
    Search for users by email or username (admin-only endpoint)
    """
    # Verify admin access
    verify_admin(current_user)
    
    if not email and not username:
        raise HTTPException(status_code=400, detail="Provide email or username to search")

    # Create base query
    query = db.query(User)
    
    # Apply filters if provided
    if email:
        pattern = f"%{email}%" if contains else f"{email}%"
        query = query.filter(User.email.ilike(pattern))
    if username:
        pattern = f"%{username}%" if contains else f"{username}%"
        query = query.filter(User.username.ilike(pattern))
    
    # Get results
    users = query.order_by(User.account_id).offset(skip).limit(limit).all()
    return users 


@router.post("/trivia/upload-questions")
async def upload_questions_csv(
    mode_id: str = Query(..., description="Mode ID (e.g., 'free_mode')"),
    file: UploadFile = File(..., description="CSV file with questions"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Upload CSV file with questions for a specific mode.
    CSV should have columns: question, option_a, option_b, option_c, option_d, correct_answer,
    fill_in_answer, hint, explanation, category, country, difficulty_level, picture_url
    """
    verify_admin(current_user)
    
    # Verify mode exists
    mode_config = get_mode_config(db, mode_id)
    if not mode_config:
        raise HTTPException(status_code=404, detail=f"Mode '{mode_id}' not found")
    
    # Read file content with size cap to avoid large uploads exhausting memory
    file_content = await file.read(MAX_QUESTION_UPLOAD_BYTES + 1)
    if len(file_content) > MAX_QUESTION_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"CSV file too large (max {MAX_QUESTION_UPLOAD_BYTES} bytes)"
        )
    
    try:
        # Parse CSV
        questions = parse_csv_questions(file_content, mode_id)
        
        # Save questions
        result = save_questions_to_mode(db, questions, mode_id)
        
        return {
            'success': True,
            'saved_count': result['saved_count'],
            'duplicate_count': result['duplicate_count'],
            'error_count': result['error_count'],
            'errors': result['errors'][:10]  # Limit errors shown
        }
    except ValueError as e:
        logging.warning("Invalid CSV upload", exc_info=True)
        raise HTTPException(status_code=400, detail="Invalid CSV file")


@router.get("/trivia/modes")
async def list_trivia_modes(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    List all trivia modes.
    """
    verify_admin(current_user)
    
    modes = db.query(TriviaModeConfig).all()
    
    result = []
    for mode in modes:
        try:
            reward_dist = json.loads(mode.reward_distribution) if mode.reward_distribution else {}
            ad_config = json.loads(mode.ad_config) if mode.ad_config else {}
            survey_config = json.loads(mode.survey_config) if mode.survey_config else {}
            leaderboard_types = json.loads(mode.leaderboard_types) if mode.leaderboard_types else []
        except (json.JSONDecodeError, TypeError):
            reward_dist = {}
            ad_config = {}
            survey_config = {}
            leaderboard_types = []
        
        result.append({
            'mode_id': mode.mode_id,
            'mode_name': mode.mode_name,
            'questions_count': mode.questions_count,
            'amount': mode.amount,
            'reward_distribution': reward_dist,
            'ad_config': ad_config,
            'survey_config': survey_config,
            'leaderboard_types': leaderboard_types,
            'created_at': mode.created_at.isoformat() if mode.created_at else None,
            'updated_at': mode.updated_at.isoformat() if mode.updated_at else None
        })
    
    return result


@router.post("/trivia/modes")
async def create_or_update_mode(
    mode_id: str = Body(..., description="Mode ID"),
    mode_name: str = Body(..., description="Mode display name"),
    questions_count: int = Body(..., description="Number of questions per day"),
    reward_distribution: dict = Body(..., description="Reward distribution config (JSON)"),
    amount: float = Body(0.0, description="Entry fee amount"),
    leaderboard_types: list = Body(..., description="Leaderboard types (e.g., ['daily'])"),
    ad_config: Optional[dict] = Body(None, description="Ad configuration (JSON)"),
    survey_config: Optional[dict] = Body(None, description="Survey configuration (JSON)"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Create or update a trivia mode configuration.
    """
    verify_admin(current_user)
    
    # Check if mode exists
    existing = db.query(TriviaModeConfig).filter(
        TriviaModeConfig.mode_id == mode_id
    ).first()
    
    if existing:
        # Update
        existing.mode_name = mode_name
        existing.questions_count = questions_count
        existing.reward_distribution = json.dumps(reward_distribution)
        existing.amount = amount
        existing.leaderboard_types = json.dumps(leaderboard_types)
        existing.ad_config = json.dumps(ad_config) if ad_config else None
        existing.survey_config = json.dumps(survey_config) if survey_config else None
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return {'success': True, 'message': f'Mode {mode_id} updated', 'mode': existing}
    else:
        # Create
        new_mode = TriviaModeConfig(
            mode_id=mode_id,
            mode_name=mode_name,
            questions_count=questions_count,
            reward_distribution=json.dumps(reward_distribution),
            amount=amount,
            leaderboard_types=json.dumps(leaderboard_types),
            ad_config=json.dumps(ad_config) if ad_config else None,
            survey_config=json.dumps(survey_config) if survey_config else None
        )
        db.add(new_mode)
        db.commit()
        db.refresh(new_mode)
        return {'success': True, 'message': f'Mode {mode_id} created', 'mode': new_mode}


@router.post("/trivia/free-mode/trigger-draw")
async def trigger_free_mode_draw(
    draw_date: Optional[str] = Body(None, description="Draw date (YYYY-MM-DD). Defaults to yesterday."),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Manually trigger draw for free mode.
    Calculates winners, distributes gems, and cleans up old leaderboard.
    """
    verify_admin(current_user)
    
    # Parse draw_date
    if draw_date:
        try:
            target_date = date.fromisoformat(draw_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        from utils.trivia_mode_service import get_active_draw_date
        target_date = get_active_draw_date() - date.resolution  # Yesterday's draw
    
    # Check if draw already performed
    from models import TriviaFreeModeWinners
    existing_draw = db.query(TriviaFreeModeWinners).filter(
        TriviaFreeModeWinners.draw_date == target_date
    ).first()
    
    if existing_draw:
        return {
            'status': 'already_performed',
            'draw_date': target_date.isoformat(),
            'message': f'Draw for {target_date} has already been performed'
        }
    
    # Get mode config
    mode_config = get_mode_config(db, 'free_mode')
    if not mode_config:
        raise HTTPException(status_code=404, detail="Free mode config not found")
    
    # Get eligible participants
    participants = get_eligible_participants_free_mode(db, target_date)
    
    if not participants:
        return {
            'status': 'no_participants',
            'draw_date': target_date.isoformat(),
            'message': f'No eligible participants for draw on {target_date}'
        }
    
    # Rank participants
    ranked_participants = rank_participants_by_completion(participants)
    
    # Calculate reward distribution
    reward_info = calculate_reward_distribution(mode_config, len(ranked_participants))
    winner_count = reward_info['winner_count']
    gem_amounts = reward_info['gem_amounts']
    
    # Select winners
    if len(ranked_participants) <= winner_count:
        winners_list = ranked_participants
    else:
        winners_list = ranked_participants[:winner_count]
    
    # Prepare winners with gem amounts
    winners = []
    for i, participant in enumerate(winners_list):
        winners.append({
            'account_id': participant['account_id'],
            'username': participant['username'],
            'position': i + 1,
            'gems_awarded': gem_amounts[i] if i < len(gem_amounts) else 0,
            'completed_at': participant['third_question_completed_at']
        })
    
    # Distribute rewards
    distribution_result = distribute_rewards_to_winners(db, winners, mode_config, target_date)
    
    # Cleanup old leaderboard (previous draw date)
    previous_draw_date = target_date - date.resolution
    cleanup_old_leaderboard(db, previous_draw_date)
    
    return {
        'status': 'success',
        'draw_date': target_date.isoformat(),
        'total_participants': len(ranked_participants),
        'total_winners': len(winners),
        'total_gems_awarded': distribution_result['total_gems_awarded'],
        'winners': winners
    }


@router.post("/trivia/free-mode/allocate-questions")
async def allocate_free_mode_questions_manual(
    target_date: Optional[str] = Body(None, description="Target date (YYYY-MM-DD). Defaults to active draw date."),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Manually trigger question allocation for free mode.
    Allocates questions from trivia_questions_free_mode to trivia_questions_free_mode_daily.
    """
    verify_admin(current_user)
    
    from utils.trivia_mode_service import get_active_draw_date, get_date_range_for_query, get_mode_config
    from models import TriviaQuestionsFreeMode, TriviaQuestionsFreeModeDaily
    import random
    
    # Parse target_date or use active draw date
    if target_date:
        try:
            target = date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        target = get_active_draw_date()
    
    # Get mode config
    mode_config = get_mode_config(db, 'free_mode')
    if not mode_config:
        raise HTTPException(status_code=404, detail="Free mode config not found")
    
    questions_count = mode_config.questions_count
    
    # Get date range for the target date
    start_datetime, end_datetime = get_date_range_for_query(target)
    
    # Check if questions already allocated for this date
    existing_questions = db.query(TriviaQuestionsFreeModeDaily).filter(
        TriviaQuestionsFreeModeDaily.date >= start_datetime,
        TriviaQuestionsFreeModeDaily.date <= end_datetime
    ).count()
    
    if existing_questions > 0:
        return {
            'status': 'already_allocated',
            'target_date': target.isoformat(),
            'existing_count': existing_questions,
            'message': f'Questions already allocated for {target}'
        }
    
    # Get available questions (prefer unused)
    unused_query = db.query(TriviaQuestionsFreeMode).filter(
        TriviaQuestionsFreeMode.is_used == False
    )
    unused_count = unused_query.count()
    
    # If not enough unused questions, get any questions
    if unused_count < questions_count:
        all_query = db.query(TriviaQuestionsFreeMode)
        all_count = all_query.count()
        if all_count < questions_count:
            raise HTTPException(
                status_code=400,
                detail=f"Not enough questions available. Need {questions_count}, have {all_count}"
            )
        available_questions = _select_random_rows(all_query, questions_count, TriviaQuestionsFreeMode.id)
    else:
        available_questions = _select_random_rows(unused_query, questions_count, TriviaQuestionsFreeMode.id)
    
    if not available_questions:
        raise HTTPException(status_code=400, detail="No questions available to allocate")
    
    # Allocate questions to daily pool
    allocated_count = 0
    for i, question in enumerate(available_questions[:questions_count], 1):
        daily_question = TriviaQuestionsFreeModeDaily(
            date=start_datetime,
            question_id=question.id,
            question_order=i,
            is_used=False
        )
        db.add(daily_question)
        # Mark question as used
        question.is_used = True
        allocated_count += 1
    
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {
            'status': 'already_allocated',
            'target_date': target.isoformat(),
            'existing_count': existing_questions,
            'message': f'Questions already allocated for {target}'
        }
    
    return {
        'status': 'success',
        'target_date': target.isoformat(),
        'allocated_count': allocated_count,
        'questions_count': questions_count,
        'message': f'Successfully allocated {allocated_count} questions for {target}'
    }


@router.post("/trivia/bronze-mode/trigger-draw")
async def trigger_bronze_mode_draw(
    draw_date: Optional[str] = Body(None, description="Draw date (YYYY-MM-DD). Defaults to yesterday."),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Manually trigger draw for $5 mode.
    Calculates winners, distributes money, and cleans up old leaderboard.
    """
    verify_admin(current_user)
    
    # Parse draw_date
    if draw_date:
        try:
            target_date = date.fromisoformat(draw_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        from utils.trivia_mode_service import get_active_draw_date
        target_date = get_active_draw_date() - date.resolution  # Yesterday's draw
    
    # Check if draw already performed
    from models import TriviaBronzeModeWinners
    existing_draw = db.query(TriviaBronzeModeWinners).filter(
        TriviaBronzeModeWinners.draw_date == target_date
    ).first()
    
    if existing_draw:
        return {
            'status': 'already_performed',
            'draw_date': target_date.isoformat(),
            'message': f'Draw for {target_date} has already been performed'
        }
    
    # Use generic draw service
    from utils.mode_draw_service import execute_mode_draw
    from utils.bronze_mode_service import (
        distribute_rewards_to_winners_bronze_mode,
        cleanup_old_leaderboard_bronze_mode
    )
    
    result = execute_mode_draw(db, 'bronze', target_date)
    
    if result['status'] == 'no_participants':
        return {
            'status': 'no_participants',
            'draw_date': target_date.isoformat(),
            'message': f'No eligible participants for draw on {target_date}'
        }
    
    if result['status'] != 'success':
        raise HTTPException(
            status_code=400,
            detail=result.get('message', 'Error executing draw')
        )
    
    # Distribute rewards
    mode_config = get_mode_config(db, 'bronze')
    if not mode_config:
        raise HTTPException(status_code=404, detail="Bronze mode config not found")
    
    winners = result.get('winners', [])
    total_pool = result.get('total_pool', 0.0)
    distribution_result = distribute_rewards_to_winners_bronze_mode(
        db, winners, target_date, total_pool
    )
    
    # Cleanup old leaderboard
    previous_draw_date = target_date - date.resolution
    cleanup_old_leaderboard_bronze_mode(db, previous_draw_date)
    
    return {
        'status': 'success',
        'draw_date': target_date.isoformat(),
        'total_participants': result.get('total_participants', 0),
        'total_winners': len(winners),
        'total_money_awarded': distribution_result.get('total_money_awarded', 0.0),
        'winners': winners
    }


@router.post("/trivia/bronze-mode/allocate-questions")
async def allocate_bronze_mode_questions_manual(
    target_date: Optional[str] = Body(None, description="Target date (YYYY-MM-DD). Defaults to active draw date."),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Manually trigger question allocation for bronze mode.
    Allocates one question from trivia_questions_bronze_mode to trivia_questions_bronze_mode_daily.
    """
    verify_admin(current_user)
    
    from utils.trivia_mode_service import get_active_draw_date, get_date_range_for_query, get_mode_config
    from models import TriviaQuestionsBronzeMode, TriviaQuestionsBronzeModeDaily
    import random
    
    # Parse target_date or use active draw date
    if target_date:
        try:
            target = date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        target = get_active_draw_date()
    
    # Get mode config
    mode_config = get_mode_config(db, 'bronze')
    if not mode_config:
        raise HTTPException(status_code=404, detail="Bronze mode config not found")
    
    # Get date range for the target date
    start_datetime, end_datetime = get_date_range_for_query(target)
    
    # Check if question already allocated for this date
    existing_question = db.query(TriviaQuestionsBronzeModeDaily).filter(
        TriviaQuestionsBronzeModeDaily.date >= start_datetime,
        TriviaQuestionsBronzeModeDaily.date <= end_datetime
    ).count()
    
    if existing_question > 0:
        return {
            'status': 'already_allocated',
            'target_date': target.isoformat(),
            'existing_count': existing_question,
            'message': f'Question already allocated for {target}'
        }
    
    # Get available questions (prefer unused)
    unused_query = db.query(TriviaQuestionsBronzeMode).filter(
        TriviaQuestionsBronzeMode.is_used == False
    )
    unused_count = unused_query.count()
    
    # If not enough unused questions, get any questions
    if unused_count < 1:
        all_query = db.query(TriviaQuestionsBronzeMode)
        all_count = all_query.count()
        if all_count < 1:
            raise HTTPException(
                status_code=400,
                detail="No questions available for bronze mode"
            )
        selected_questions = _select_random_rows(all_query, 1, TriviaQuestionsBronzeMode.id)
    else:
        selected_questions = _select_random_rows(unused_query, 1, TriviaQuestionsBronzeMode.id)
    
    if not selected_questions:
        raise HTTPException(status_code=400, detail="No questions available for bronze mode")
    selected_question = selected_questions[0]
    
    # Allocate question to daily pool
    daily_question = TriviaQuestionsBronzeModeDaily(
        date=start_datetime,
        question_id=selected_question.id,
        question_order=1,  # Always 1 for bronze mode
        is_used=False
    )
    db.add(daily_question)
    # Mark question as used
    selected_question.is_used = True
    
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {
            'status': 'already_allocated',
            'target_date': target.isoformat(),
            'existing_count': existing_question,
            'message': f'Question already allocated for {target}'
        }
    
    return {
        'status': 'success',
        'target_date': target.isoformat(),
        'allocated_count': 1,
        'question_id': selected_question.id,
        'message': f'Successfully allocated question for {target}'
    }


@router.get("/subscriptions/check")
async def check_subscription_status(
    plan_id: Optional[int] = Query(None, description="Subscription plan ID to check. If not provided, checks all plans."),
    price_usd: Optional[float] = Query(None, description="Filter by price in USD (e.g., 5.0 for $5 plans)"),
    user_id: Optional[int] = Query(None, description="User account ID to check. If not provided, checks all users."),
    plan_skip: int = Query(0, ge=0, description="Plans to skip"),
    plan_limit: int = Query(100, ge=1, le=500, description="Max plans to return"),
    sub_skip: int = Query(0, ge=0, description="Subscriptions to skip"),
    sub_limit: int = Query(100, ge=1, le=1000, description="Max subscriptions to return"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Check subscription plan and user subscription status.
    Generic endpoint that can check any subscription plan by plan_id or price.
    """
    verify_admin(current_user)
    
    # Build query for plans
    plan_query = db.query(SubscriptionPlan)
    
    if plan_id:
        plan_query = plan_query.filter(SubscriptionPlan.id == plan_id)
    elif price_usd:
        plan_query = plan_query.filter(
            (SubscriptionPlan.price_usd == price_usd) |
            (SubscriptionPlan.unit_amount_minor == int(price_usd * 100))
        )
    
    plans = plan_query.order_by(SubscriptionPlan.id).offset(plan_skip).limit(plan_limit).all()
    
    result = {
        'plans_found': len(plans),
        'plans': [],
        'subscriptions': []
    }
    
    for plan in plans:
        result['plans'].append({
            'id': plan.id,
            'name': plan.name,
            'description': plan.description,
            'price_usd': plan.price_usd,
            'unit_amount_minor': plan.unit_amount_minor,
            'currency': plan.currency,
            'interval': plan.interval,
            'interval_count': plan.interval_count,
            'stripe_price_id': plan.stripe_price_id
        })
    
    # Check subscriptions with joins to avoid N+1 queries
    sub_query = db.query(UserSubscription, User, SubscriptionPlan).join(
        User, User.account_id == UserSubscription.user_id
    ).join(
        SubscriptionPlan, SubscriptionPlan.id == UserSubscription.plan_id
    )
    
    if plan_id:
        sub_query = sub_query.filter(UserSubscription.plan_id == plan_id)
    elif price_usd:
        sub_query = sub_query.filter(
            (SubscriptionPlan.price_usd == price_usd) |
            (SubscriptionPlan.unit_amount_minor == int(price_usd * 100))
        )
    
    if user_id:
        sub_query = sub_query.filter(UserSubscription.user_id == user_id)
    
    subscriptions = sub_query.order_by(UserSubscription.id).offset(sub_skip).limit(sub_limit).all()
    
    for sub, user, plan in subscriptions:
        result['subscriptions'].append({
            'user_id': sub.user_id,
            'username': user.username if user else None,
            'subscription_id': sub.id,
            'plan_id': sub.plan_id,
            'plan_name': plan.name if plan else None,
            'plan_price_usd': plan.price_usd if plan else None,
            'status': sub.status,
            'current_period_start': sub.current_period_start.isoformat() if sub.current_period_start else None,
            'current_period_end': sub.current_period_end.isoformat() if sub.current_period_end else None,
            'is_active': sub.status == 'active' and (sub.current_period_end is None or sub.current_period_end > datetime.utcnow())
        })
    
    return result


class CreateSubscriptionPlanRequest(BaseModel):
    name: str = "$5 Monthly Subscription"
    description: Optional[str] = "$5 monthly subscription for trivia bronze mode access"
    price_usd: float = 5.0
    unit_amount_minor: Optional[int] = 500  # Will be calculated from price_usd if not provided
    currency: str = "usd"
    interval: str = "month"  # month, year, etc.
    interval_count: int = 1
    billing_interval: Optional[str] = None  # Will use interval if not provided
    stripe_price_id: Optional[str] = None
    livemode: bool = False


@router.post("/subscriptions/create-plan")
async def create_subscription_plan(
    request: CreateSubscriptionPlanRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Create a subscription plan if it doesn't exist.
    Generic endpoint that can create any subscription plan with specified price and interval.
    """
    verify_admin(current_user)
    
    # Calculate unit_amount_minor from price_usd if not provided
    unit_amount_minor = request.unit_amount_minor
    if unit_amount_minor is None:
        unit_amount_minor = int(request.price_usd * 100)
    
    # Use billing_interval from request or default to interval
    billing_interval = request.billing_interval or request.interval
    
    # Check if plan already exists (by price and interval)
    existing = db.query(SubscriptionPlan).filter(
        (SubscriptionPlan.unit_amount_minor == unit_amount_minor) | 
        (SubscriptionPlan.price_usd == request.price_usd),
        SubscriptionPlan.interval == request.interval
    ).first()
    
    if existing:
        return {
            'success': False,
            'message': f'Subscription plan with price ${request.price_usd} and interval {request.interval} already exists (ID: {existing.id})',
            'plan_id': existing.id,
            'plan': {
                'id': existing.id,
                'name': existing.name,
                'price_usd': existing.price_usd,
                'unit_amount_minor': existing.unit_amount_minor,
                'interval': existing.interval
            }
        }
    
    # Create new plan
    plan = SubscriptionPlan(
        name=request.name,
        description=request.description or f"{request.name} - ${request.price_usd:.2f} per {request.interval}",
        price_usd=request.price_usd,
        billing_interval=billing_interval,
        unit_amount_minor=unit_amount_minor,
        currency=request.currency,
        interval=request.interval,
        interval_count=request.interval_count,
        stripe_price_id=request.stripe_price_id,
        livemode=request.livemode
    )
    
    db.add(plan)
    db.commit()
    db.refresh(plan)
    
    return {
        'success': True,
        'message': f'Subscription plan created successfully',
        'plan': {
            'id': plan.id,
            'name': plan.name,
            'description': plan.description,
            'price_usd': plan.price_usd,
            'unit_amount_minor': plan.unit_amount_minor,
            'currency': plan.currency,
            'interval': plan.interval,
            'interval_count': plan.interval_count,
            'stripe_price_id': plan.stripe_price_id
        }
    }


class CreateSubscriptionRequest(BaseModel):
    user_id: Optional[int] = Field(None, description="User account ID to create subscription for. If not provided, creates for current user.")
    plan_id: int = Field(..., description="Subscription plan ID (required).")


@router.post("/subscriptions/create-subscription")
async def create_subscription_for_user(
    request: CreateSubscriptionRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Create an active subscription for a user (for testing/admin purposes).
    This creates a UserSubscription record linking the user to a plan.
    If user_id is not provided, creates subscription for the current user.
    Subscription duration is fixed at 30 days.
    """
    verify_admin(current_user)
    
    # If user_id not provided, use current user
    user_id = request.user_id
    if user_id is None:
        user_id = current_user.account_id
    
    # Find the subscription plan
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == request.plan_id).first()
    if not plan:
        raise HTTPException(
            status_code=404,
            detail=f"Subscription plan with ID {request.plan_id} not found"
        )
    
    # Check if user exists
    user = db.query(User).filter(User.account_id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=404,
            detail=f"User with ID {user_id} not found"
        )
    
    # Check if user already has an active subscription for this plan
    existing = db.query(UserSubscription).filter(
        UserSubscription.user_id == user_id,
        UserSubscription.plan_id == plan.id,
        UserSubscription.status == 'active'
    ).first()
    
    if existing:
        return {
            'success': False,
            'message': f'User already has an active subscription for plan "{plan.name}" (ID: {existing.id})',
            'subscription_id': existing.id
        }
    
    # Create subscription (set to expire after 30 days)
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    period_end = now + timedelta(days=30)
    
    subscription = UserSubscription(
        user_id=user_id,
        plan_id=plan.id,
        status='active',
        current_period_start=now,
        current_period_end=period_end,
        livemode=False
    )
    
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    
    return {
        'success': True,
        'message': f'Active subscription created for user {user_id}',
        'subscription': {
            'id': subscription.id,
            'user_id': subscription.user_id,
            'username': user.username,
            'plan_id': subscription.plan_id,
            'plan_name': plan.name,
            'plan_price_usd': plan.price_usd,
            'status': subscription.status,
            'current_period_start': subscription.current_period_start.isoformat() if subscription.current_period_start else None,
            'current_period_end': subscription.current_period_end.isoformat() if subscription.current_period_end else None
        }
    }

# ======== Store Admin Endpoints ========

@router.post("/gem-packages", response_model=GemPackageResponse)
async def create_gem_package(
    package: GemPackageRequest = Body(..., description="Gem package details"),
    claims: dict = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Admin endpoint to create a new gem package"""
    new_package = GemPackageConfig(
        price_minor=package.price_minor,
        gems_amount=package.gems_amount,
        is_one_time=package.is_one_time,
        description=package.description,
        bucket=package.bucket,
        object_key=package.object_key,
        mime_type=package.mime_type
    )
    
    db.add(new_package)
    db.commit()
    db.refresh(new_package)
    
    # Generate presigned URL if bucket and object_key are present
    signed_url = None
    if new_package.bucket and new_package.object_key:
        try:
            signed_url = presign_get(new_package.bucket, new_package.object_key, expires=900)
        except Exception as e:
            logging.error(f"Failed to presign gem package {new_package.id}: {e}", exc_info=True)
    
    return GemPackageResponse(
        id=new_package.id,
        price_usd=new_package.price_usd,
        gems_amount=new_package.gems_amount,
        is_one_time=new_package.is_one_time,
        description=new_package.description,
        url=signed_url,
        mime_type=new_package.mime_type,
        created_at=new_package.created_at,
        updated_at=new_package.updated_at
    )

@router.put("/gem-packages/{package_id}", response_model=GemPackageResponse)
async def update_gem_package(
    package_id: int = Path(..., description="ID of the gem package to update"),
    package: GemPackageRequest = Body(..., description="Updated gem package details"),
    claims: dict = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Admin endpoint to update an existing gem package"""
    db_package = db.query(GemPackageConfig).filter(GemPackageConfig.id == package_id).first()
    if not db_package:
        raise HTTPException(status_code=404, detail=f"Gem package with ID {package_id} not found")
    
    # Update fields
    db_package.price_minor = package.price_minor
    db_package.gems_amount = package.gems_amount
    db_package.is_one_time = package.is_one_time
    db_package.description = package.description
    db_package.bucket = package.bucket
    db_package.object_key = package.object_key
    db_package.mime_type = package.mime_type
    db_package.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(db_package)
    
    # Generate presigned URL if bucket and object_key are present
    signed_url = None
    if db_package.bucket and db_package.object_key:
        try:
            signed_url = presign_get(db_package.bucket, db_package.object_key, expires=900)
        except Exception as e:
            logging.error(f"Failed to presign gem package {db_package.id}: {e}", exc_info=True)
    
    return GemPackageResponse(
        id=db_package.id,
        price_usd=db_package.price_usd,
        gems_amount=db_package.gems_amount,
        is_one_time=db_package.is_one_time,
        description=db_package.description,
        url=signed_url,
        mime_type=db_package.mime_type,
        created_at=db_package.created_at,
        updated_at=db_package.updated_at
    )

@router.delete("/gem-packages/{package_id}", response_model=Dict[str, Any])
async def delete_gem_package(
    package_id: int = Path(..., description="ID of the gem package to delete"),
    claims: dict = Depends(get_admin_user),
    db: Session = Depends(get_db)
):
    """Admin endpoint to delete a gem package"""
    db_package = db.query(GemPackageConfig).filter(GemPackageConfig.id == package_id).first()
    if not db_package:
        raise HTTPException(status_code=404, detail=f"Gem package with ID {package_id} not found")
    
    db.delete(db_package)
    db.commit()
    
    return {"message": f"Gem package with ID {package_id} deleted successfully"}

# ======== Badges Admin Endpoints ========

def validate_badge_url_is_public(image_url: str) -> bool:
    """Validate that badge image_url is a public S3 URL (not a presigned URL)."""
    if not image_url:
        return False
    
    presigned_indicators = ['X-Amz-Algorithm', 'X-Amz-Credential', 'X-Amz-Signature', 'X-Amz-Date']
    if any(indicator in image_url for indicator in presigned_indicators):
        logging.warning(f"Badge URL appears to be presigned (should be public): {image_url[:100]}...")
        return False
    
    public_url_patterns = ['s3.amazonaws.com', 's3.', 'amazonaws.com', 'cdn.', '.com/', '.org/']
    if any(pattern in image_url for pattern in public_url_patterns):
        return True
    
    if image_url.startswith('http://') or image_url.startswith('https://'):
        return True
    
    return False

@router.post("/badges", response_model=BadgeResponse)
async def create_badge(
    badge: BadgeCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """Admin endpoint to create a new badge (stored in TriviaModeConfig)."""
    # Validate that the URL is public (warn if not, but allow)
    if not validate_badge_url_is_public(badge.image_url):
        logging.warning(
            f"Creating badge with URL that appears non-public: {badge.image_url[:100]}. "
            f"Badges should use public S3 URLs for optimal performance."
        )
    
    # Use provided ID or generate a new one (this will be mode_id)
    badge_id = badge.id if badge.id else str(uuid.uuid4())
    
    # Check if a mode config with this ID already exists
    if badge.id:
        existing = db.query(TriviaModeConfig).filter(TriviaModeConfig.mode_id == badge_id).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Mode config with ID {badge_id} already exists. Use PUT to update badge fields."
            )
    
    # Create a new TriviaModeConfig entry for the badge
    # Note: Some fields are required for TriviaModeConfig, so we set defaults
    new_mode_config = TriviaModeConfig(
        mode_id=badge_id,
        mode_name=badge.name,
        questions_count=1,  # Default, can be updated later
        reward_distribution="{}",  # Default empty JSON
        amount=0.0,  # Default
        leaderboard_types="[]",  # Default empty array
        badge_image_url=badge.image_url,
        badge_description=badge.description,
        badge_level=badge.level,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    db.add(new_mode_config)
    db.commit()
    db.refresh(new_mode_config)
    
    logging.info(f"Created badge {badge_id} ({badge.name}) with public URL: {badge.image_url[:80]}...")
    
    # Return in BadgeResponse format
    return BadgeResponse(
        id=new_mode_config.mode_id,
        name=new_mode_config.mode_name,
        description=new_mode_config.badge_description,
        image_url=new_mode_config.badge_image_url,
        level=new_mode_config.badge_level or 0,
        created_at=new_mode_config.created_at
    )

@router.put("/badges/{badge_id}", response_model=BadgeResponse)
async def update_badge(
    badge_id: str = Path(..., description="The ID of the badge to update (mode_id)"),
    badge_update: BadgeUpdate = Body(..., description="Updated badge data"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """Admin endpoint to update an existing badge (stored in TriviaModeConfig)."""
    # Find the mode config (badge is stored here)
    mode_config = db.query(TriviaModeConfig).filter(TriviaModeConfig.mode_id == badge_id).first()
    if not mode_config:
        raise HTTPException(status_code=404, detail=f"Badge with ID {badge_id} not found")
    
    # Validate that the new URL is public (warn if not, but allow)
    if not validate_badge_url_is_public(badge_update.image_url):
        logging.warning(
            f"Updating badge {badge_id} with URL that appears non-public: {badge_update.image_url[:100]}. "
            f"Badges should use public S3 URLs for optimal performance."
        )
    
    # Update badge fields in TriviaModeConfig
    mode_config.mode_name = badge_update.name
    mode_config.badge_description = badge_update.description
    mode_config.badge_image_url = badge_update.image_url
    mode_config.badge_level = badge_update.level
    mode_config.updated_at = datetime.utcnow()
    
    # Count how many users have this badge (for informational purposes)
    users_updated = db.query(User).filter(User.badge_id == badge_id).count()
    
    db.commit()
    db.refresh(mode_config)
    
    logging.info(
        f"Updated badge {badge_id} ({badge_update.name}). "
        f"Image URL changed, {users_updated} users updated with new badge image URL."
    )
    
    # Return in BadgeResponse format
    return BadgeResponse(
        id=mode_config.mode_id,
        name=mode_config.mode_name,
        description=mode_config.badge_description,
        image_url=mode_config.badge_image_url,
        level=mode_config.badge_level or 0,
        created_at=mode_config.created_at
    )

@router.get("/badges/assignments", response_model=Dict[str, Any])
async def get_badge_assignments(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """Admin endpoint to get badge assignment statistics"""
    # Get counts of users per badge (badges are now in TriviaModeConfig)
    result = {}
    counts = dict(
        db.query(User.badge_id, func.count(User.account_id))
        .group_by(User.badge_id)
        .all()
    )
    badges = db.query(TriviaModeConfig).filter(TriviaModeConfig.badge_image_url.isnot(None)).all()
    
    for mode_config in badges:
        result[mode_config.mode_id] = {
            "badge_name": mode_config.mode_name,
            "user_count": counts.get(mode_config.mode_id, 0)
        }
    
    # Also get count of users with no badge
    no_badge_count = counts.get(None, 0)
    result["no_badge"] = {
        "badge_name": "No Badge",
        "user_count": no_badge_count
    }
    
    return {
        "assignments": result,
        "total_users": sum(counts.values())
    }

# ======== Cosmetics Admin Endpoints ========

@router.post("/avatars", response_model=AvatarResponse)
async def create_avatar(
    avatar: AvatarCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """Admin endpoint to create a new avatar"""
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
        price_gems=avatar.price_gems,
        price_usd=avatar.price_usd,
        is_premium=avatar.is_premium,
        bucket=avatar.bucket,
        object_key=avatar.object_key,
        mime_type=avatar.mime_type,
        created_at=datetime.utcnow()
    )
    
    db.add(new_avatar)
    db.commit()
    db.refresh(new_avatar)
    
    return new_avatar

@router.put("/avatars/{avatar_id}", response_model=AvatarResponse)
async def update_avatar(
    avatar_id: str = Path(..., description="The ID of the avatar to update"),
    avatar_update: AvatarCreate = Body(..., description="Updated avatar data"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """Admin endpoint to update an existing avatar"""
    # Find the avatar
    avatar = db.query(Avatar).filter(Avatar.id == avatar_id).first()
    if not avatar:
        raise HTTPException(status_code=404, detail=f"Avatar with ID {avatar_id} not found")
    
    # Update avatar fields
    avatar.name = avatar_update.name
    avatar.description = avatar_update.description
    avatar.price_gems = avatar_update.price_gems
    avatar.price_minor = avatar_update.price_minor
    avatar.is_premium = avatar_update.is_premium
    avatar.bucket = avatar_update.bucket
    avatar.object_key = avatar_update.object_key
    avatar.mime_type = avatar_update.mime_type
    
    db.commit()
    db.refresh(avatar)
    
    return avatar

@router.delete("/avatars/{avatar_id}", response_model=dict)
async def delete_avatar(
    avatar_id: str = Path(..., description="The ID of the avatar to delete"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """Admin endpoint to delete an avatar"""
    # Find the avatar
    avatar = db.query(Avatar).filter(Avatar.id == avatar_id).first()
    if not avatar:
        raise HTTPException(status_code=404, detail=f"Avatar with ID {avatar_id} not found")
    
    # Remove any references in user_avatars table
    db.query(UserAvatar).filter(UserAvatar.avatar_id == avatar_id).delete(synchronize_session=False)
    
    # Remove any users who have this as selected avatar
    db.query(User).filter(User.selected_avatar_id == avatar_id).update(
        {User.selected_avatar_id: None},
        synchronize_session=False
    )
    
    # Delete the avatar
    db.delete(avatar)
    db.commit()
    
    return {"status": "success", "message": f"Avatar with ID {avatar_id} deleted successfully"}

@router.post("/frames", response_model=FrameResponse)
async def create_frame(
    frame: FrameCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """Admin endpoint to create a new frame"""
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
        price_gems=frame.price_gems,
        price_usd=frame.price_usd,
        is_premium=frame.is_premium,
        bucket=frame.bucket,
        object_key=frame.object_key,
        mime_type=frame.mime_type,
        created_at=datetime.utcnow()
    )
    
    db.add(new_frame)
    db.commit()
    db.refresh(new_frame)
    
    return new_frame

@router.put("/frames/{frame_id}", response_model=FrameResponse)
async def update_frame(
    frame_id: str = Path(..., description="The ID of the frame to update"),
    frame_update: FrameCreate = Body(..., description="Updated frame data"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """Admin endpoint to update an existing frame"""
    # Find the frame
    frame = db.query(Frame).filter(Frame.id == frame_id).first()
    if not frame:
        raise HTTPException(status_code=404, detail=f"Frame with ID {frame_id} not found")
    
    # Update frame fields
    frame.name = frame_update.name
    frame.description = frame_update.description
    frame.price_gems = frame_update.price_gems
    frame.price_minor = frame_update.price_minor
    frame.is_premium = frame_update.is_premium
    frame.bucket = frame_update.bucket
    frame.object_key = frame_update.object_key
    frame.mime_type = frame_update.mime_type
    
    db.commit()
    db.refresh(frame)
    
    return frame

@router.delete("/frames/{frame_id}", response_model=dict)
async def delete_frame(
    frame_id: str = Path(..., description="The ID of the frame to delete"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """Admin endpoint to delete a frame"""
    # Find the frame
    frame = db.query(Frame).filter(Frame.id == frame_id).first()
    if not frame:
        raise HTTPException(status_code=404, detail=f"Frame with ID {frame_id} not found")
    
    # Remove any references in user_frames table
    db.query(UserFrame).filter(UserFrame.frame_id == frame_id).delete(synchronize_session=False)
    
    # Remove any users who have this as selected frame
    db.query(User).filter(User.selected_frame_id == frame_id).update(
        {User.selected_frame_id: None},
        synchronize_session=False
    )
    
    # Delete the frame
    db.delete(frame)
    db.commit()
    
    return {"status": "success", "message": f"Frame with ID {frame_id} deleted successfully"}

@router.post("/avatars/import", response_model=BulkImportResponse)
async def import_avatars_from_json(
    json_data: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """Bulk import avatars from a JSON file or import a single avatar."""
    # Check if this is a single avatar or a collection
    if "avatars" in json_data:
        avatars = json_data.get("avatars", [])
    elif "id" in json_data and "name" in json_data:
        avatars = [json_data]
    else:
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
            avatar_id = avatar_data.get("id", str(uuid.uuid4()))
            existing = db.query(Avatar).filter(Avatar.id == avatar_id).first()
            if existing:
                for key, value in avatar_data.items():
                    if key != "id" and hasattr(existing, key):
                        setattr(existing, key, value)
            else:
                new_avatar = Avatar(
                    id=avatar_id,
                    name=avatar_data.get("name", "Unnamed Avatar"),
                    description=avatar_data.get("description"),
                    price_gems=avatar_data.get("price_gems"),
                    price_usd=avatar_data.get("price_usd"),
                    is_premium=avatar_data.get("is_premium", False),
                    bucket=avatar_data.get("bucket"),
                    object_key=avatar_data.get("object_key"),
                    mime_type=avatar_data.get("mime_type"),
                    created_at=datetime.utcnow()
                )
                db.add(new_avatar)
            imported += 1
        except Exception as e:
            name = avatar_data.get("name", "unknown")
            logging.error(f"Error importing avatar {name}", exc_info=True)
            errors.append(f"Error importing avatar {name}")
    
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logging.error("Database error while importing avatars", exc_info=True)
        return BulkImportResponse(
            status="error",
            message="Database error",
            imported_count=0,
            errors=["Database error"]
        )
    
    return BulkImportResponse(
        status="success",
        message=f"Successfully imported {imported} avatars",
        imported_count=imported,
        errors=errors
    )

@router.post("/frames/import", response_model=BulkImportResponse)
async def import_frames_from_json(
    json_data: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """Bulk import frames from a JSON file or import a single frame."""
    # Check if this is a single frame or a collection
    if "frames" in json_data:
        frames = json_data.get("frames", [])
    elif "id" in json_data and "name" in json_data:
        frames = [json_data]
    else:
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
            frame_id = frame_data.get("id", str(uuid.uuid4()))
            existing = db.query(Frame).filter(Frame.id == frame_id).first()
            if existing:
                for key, value in frame_data.items():
                    if key != "id" and hasattr(existing, key):
                        setattr(existing, key, value)
            else:
                new_frame = Frame(
                    id=frame_id,
                    name=frame_data.get("name", "Unnamed Frame"),
                    description=frame_data.get("description"),
                    price_gems=frame_data.get("price_gems"),
                    price_usd=frame_data.get("price_usd"),
                    is_premium=frame_data.get("is_premium", False),
                    bucket=frame_data.get("bucket"),
                    object_key=frame_data.get("object_key"),
                    mime_type=frame_data.get("mime_type"),
                    created_at=datetime.utcnow()
                )
                db.add(new_frame)
            imported += 1
        except Exception as e:
            name = frame_data.get("name", "unknown")
            logging.error(f"Error importing frame {name}", exc_info=True)
            errors.append(f"Error importing frame {name}")
    
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logging.error("Database error while importing frames", exc_info=True)
        return BulkImportResponse(
            status="error",
            message="Database error",
            imported_count=0,
            errors=["Database error"]
        )
    
    return BulkImportResponse(
        status="success",
        message=f"Successfully imported {imported} frames",
        imported_count=imported,
        errors=errors
    )

@router.get("/avatars/stats", response_model=Dict[str, Any])
async def get_avatar_stats(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """Admin endpoint to get statistics about avatars usage"""
    from sqlalchemy import func
    
    total_avatars = db.query(Avatar).count()
    default_avatars = db.query(Avatar).filter(Avatar.is_default == True).count()
    premium_avatars = db.query(Avatar).filter(Avatar.is_premium == True).count()
    
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
        func.count(UserAvatar.avatar_id).label('purchase_count')
    ).join(
        UserAvatar, UserAvatar.avatar_id == Avatar.id
    ).group_by(
        Avatar.id, Avatar.name
    ).order_by(
        func.desc('purchase_count')
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
