import os
from datetime import date, datetime, time
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Depends, HTTPException, Body, status, Path, Query
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy.orm import Session
import pytz
import logging

from db import get_db
from models import TriviaQuestionsWinners, User, TriviaDrawConfig, TriviaModeConfig, SubscriptionPlan, UserSubscription
from routers.dependencies import get_admin_user, get_current_user, verify_admin
from rewards_logic import perform_draw
from utils.question_upload_service import parse_csv_questions, save_questions_to_mode
from utils.free_mode_rewards import (
    get_eligible_participants_free_mode, rank_participants_by_completion,
    calculate_reward_distribution, distribute_rewards_to_winners, cleanup_old_leaderboard
)
from utils.trivia_mode_service import get_mode_config
from fastapi import UploadFile, File
import json

router = APIRouter(prefix="/admin", tags=["Admin"])

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

@router.get("/draw-config", response_model=DrawConfigResponse)
async def get_draw_config(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to get the current draw configuration.
    """
    try:
        logging.info("Getting draw config from admin.py endpoint")
        config = db.query(TriviaDrawConfig).first()
        if not config:
            config = TriviaDrawConfig(
                is_custom=False,
                custom_winner_count=None
            )
            db.add(config)
            db.commit()
            db.refresh(config)
            logging.info("Created new default config")
        
        logging.info(f"Current config: is_custom={config.is_custom}, custom_winner_count={config.custom_winner_count}")
        
        # Get draw time from environment variables
        draw_time_hour = int(os.environ.get("DRAW_TIME_HOUR", "20"))
        draw_time_minute = int(os.environ.get("DRAW_TIME_MINUTE", "0"))
        draw_timezone = os.environ.get("DRAW_TIMEZONE", "US/Eastern")
        
        return DrawConfigResponse(
            is_custom=config.is_custom,
            custom_winner_count=config.custom_winner_count,
            draw_time_hour=draw_time_hour,
            draw_time_minute=draw_time_minute,
            draw_timezone=draw_timezone
        )
        
    except Exception as e:
        logging.error(f"Error getting draw configuration: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting draw configuration: {str(e)}"
        )

@router.put("/draw-config", response_model=DrawConfigResponse, operation_id="admin_update_draw_config")
async def update_draw_config(
    config: DrawConfigUpdateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to update the draw configuration.
    """
    try:
        logging.info(f"Updating draw config: {config}")
        # Validate timezone if provided
        if config.draw_timezone:
            try:
                pytz.timezone(config.draw_timezone)
            except pytz.exceptions.UnknownTimeZoneError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid timezone: {config.draw_timezone}"
                )
        
        # Get or create config in database
        db_config = db.query(TriviaDrawConfig).first()
        if not db_config:
            db_config = TriviaDrawConfig(
                is_custom=False,
                custom_winner_count=None
            )
            db.add(db_config)
            logging.info("Created new config")
        
        # Update database config
        if config.is_custom is not None:
            db_config.is_custom = config.is_custom
        
        if config.custom_winner_count is not None:
            db_config.custom_winner_count = config.custom_winner_count
        
        # Update environment variables for draw time
        if config.draw_time_hour is not None:
            os.environ["DRAW_TIME_HOUR"] = str(config.draw_time_hour)
        
        if config.draw_time_minute is not None:
            os.environ["DRAW_TIME_MINUTE"] = str(config.draw_time_minute)
        
        if config.draw_timezone:
            os.environ["DRAW_TIMEZONE"] = config.draw_timezone
        
        db.commit()
        db.refresh(db_config)
        logging.info(f"Updated config: is_custom={db_config.is_custom}, custom_winner_count={db_config.custom_winner_count}")
        
        # Get current values for response
        draw_time_hour = int(os.environ.get("DRAW_TIME_HOUR", "20"))
        draw_time_minute = int(os.environ.get("DRAW_TIME_MINUTE", "0"))
        draw_timezone = os.environ.get("DRAW_TIMEZONE", "US/Eastern")
        
        return DrawConfigResponse(
            is_custom=db_config.is_custom,
            custom_winner_count=db_config.custom_winner_count,
            draw_time_hour=draw_time_hour,
            draw_time_minute=draw_time_minute,
            draw_timezone=draw_timezone
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Error updating draw configuration: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating draw configuration: {str(e)}"
        )

@router.post("/trigger-draw", response_model=DrawResponse, operation_id="admin_trigger_draw")
async def trigger_draw(
    draw_date: date = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_admin_user)
):
    """
    Admin endpoint to manually trigger a draw for a specific date.
    """
    try:
        # If no date provided, use today's date
        if draw_date is None:
            draw_date = date.today()
            
        # Check if a draw has already been performed for this date
        existing_draw = db.query(TriviaQuestionsWinners).filter(
            TriviaQuestionsWinners.draw_date == draw_date
        ).first()
        
        if existing_draw:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Draw for {draw_date} has already been performed"
            )
        
        # Perform the draw
        result = perform_draw(db, draw_date)
        
        if result["status"] == "no_participants":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No eligible participants for draw on {draw_date}"
            )
        
        return DrawResponse(
            status=result["status"],
            draw_date=result["draw_date"],
            total_participants=result["total_participants"],
            total_winners=result["total_winners"],
            prize_pool=result["prize_pool"],
            winners=result["winners"]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error triggering draw: {str(e)}"
        )

@router.get("/users", response_model=List[UserAdminStatus])
async def get_admin_users(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Get all users with their admin status (admin-only endpoint)
    """
    # Verify admin access
    verify_admin(current_user)
    
    # Get all users with their admin status
    users = db.query(User).all()
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
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Search for users by email or username (admin-only endpoint)
    """
    # Verify admin access
    verify_admin(current_user)
    
    # Create base query
    query = db.query(User)
    
    # Apply filters if provided
    if email:
        query = query.filter(User.email.ilike(f"%{email}%"))
    if username:
        query = query.filter(User.username.ilike(f"%{username}%"))
    
    # Get results
    users = query.all()
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
    
    # Read file content
    file_content = await file.read()
    
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
        raise HTTPException(status_code=400, detail=str(e))


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
    unused_questions = db.query(TriviaQuestionsFreeMode).filter(
        TriviaQuestionsFreeMode.is_used == False
    ).all()
    
    # If not enough unused questions, get any questions
    if len(unused_questions) < questions_count:
        all_questions = db.query(TriviaQuestionsFreeMode).all()
        if len(all_questions) < questions_count:
            raise HTTPException(
                status_code=400,
                detail=f"Not enough questions available. Need {questions_count}, have {len(all_questions)}"
            )
        available_questions = random.sample(all_questions, questions_count)
    else:
        available_questions = random.sample(unused_questions, questions_count)
    
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
    
    db.commit()
    
    return {
        'status': 'success',
        'target_date': target.isoformat(),
        'allocated_count': allocated_count,
        'questions_count': questions_count,
        'message': f'Successfully allocated {allocated_count} questions for {target}'
    }


@router.post("/trivia/five-dollar-mode/trigger-draw")
async def trigger_five_dollar_mode_draw(
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
    from models import TriviaFiveDollarModeWinners
    existing_draw = db.query(TriviaFiveDollarModeWinners).filter(
        TriviaFiveDollarModeWinners.draw_date == target_date
    ).first()
    
    if existing_draw:
        return {
            'status': 'already_performed',
            'draw_date': target_date.isoformat(),
            'message': f'Draw for {target_date} has already been performed'
        }
    
    # Use generic draw service
    from utils.mode_draw_service import execute_mode_draw
    from utils.five_dollar_mode_service import (
        distribute_rewards_to_winners_five_dollar_mode,
        cleanup_old_leaderboard_five_dollar_mode
    )
    
    result = execute_mode_draw(db, 'five_dollar_mode', target_date)
    
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
    mode_config = get_mode_config(db, 'five_dollar_mode')
    if not mode_config:
        raise HTTPException(status_code=404, detail="$5 mode config not found")
    
    winners = result.get('winners', [])
    distribution_result = distribute_rewards_to_winners_five_dollar_mode(
        db, winners, mode_config, target_date
    )
    
    # Cleanup old leaderboard
    previous_draw_date = target_date - date.resolution
    cleanup_old_leaderboard_five_dollar_mode(db, previous_draw_date)
    
    return {
        'status': 'success',
        'draw_date': target_date.isoformat(),
        'total_participants': result.get('total_participants', 0),
        'total_winners': len(winners),
        'total_money_awarded': distribution_result.get('total_money_awarded', 0.0),
        'winners': winners
    }


@router.post("/trivia/five-dollar-mode/allocate-questions")
async def allocate_five_dollar_mode_questions_manual(
    target_date: Optional[str] = Body(None, description="Target date (YYYY-MM-DD). Defaults to active draw date."),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Manually trigger question allocation for $5 mode.
    Allocates one question from trivia_questions_five_dollar_mode to trivia_questions_five_dollar_mode_daily.
    """
    verify_admin(current_user)
    
    from utils.trivia_mode_service import get_active_draw_date, get_date_range_for_query, get_mode_config
    from models import TriviaQuestionsFiveDollarMode, TriviaQuestionsFiveDollarModeDaily
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
    mode_config = get_mode_config(db, 'five_dollar_mode')
    if not mode_config:
        raise HTTPException(status_code=404, detail="$5 mode config not found")
    
    # Get date range for the target date
    start_datetime, end_datetime = get_date_range_for_query(target)
    
    # Check if question already allocated for this date
    existing_question = db.query(TriviaQuestionsFiveDollarModeDaily).filter(
        TriviaQuestionsFiveDollarModeDaily.date >= start_datetime,
        TriviaQuestionsFiveDollarModeDaily.date <= end_datetime
    ).count()
    
    if existing_question > 0:
        return {
            'status': 'already_allocated',
            'target_date': target.isoformat(),
            'existing_count': existing_question,
            'message': f'Question already allocated for {target}'
        }
    
    # Get available questions (prefer unused)
    unused_questions = db.query(TriviaQuestionsFiveDollarMode).filter(
        TriviaQuestionsFiveDollarMode.is_used == False
    ).all()
    
    # If not enough unused questions, get any questions
    if len(unused_questions) < 1:
        all_questions = db.query(TriviaQuestionsFiveDollarMode).all()
        if len(all_questions) < 1:
            raise HTTPException(
                status_code=400,
                detail="No questions available for $5 mode"
            )
        selected_question = random.choice(all_questions)
    else:
        selected_question = random.choice(unused_questions)
    
    # Allocate question to daily pool
    daily_question = TriviaQuestionsFiveDollarModeDaily(
        date=start_datetime,
        question_id=selected_question.id,
        question_order=1,  # Always 1 for $5 mode
        is_used=False
    )
    db.add(daily_question)
    # Mark question as used
    selected_question.is_used = True
    
    db.commit()
    
    return {
        'status': 'success',
        'target_date': target.isoformat(),
        'allocated_count': 1,
        'question_id': selected_question.id,
        'message': f'Successfully allocated question for {target}'
    }


@router.get("/subscriptions/five-dollar/check")
async def check_five_dollar_subscription_status(
    user_id: Optional[int] = Query(None, description="User account ID to check. If not provided, checks all users."),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Check $5 subscription plan and user subscription status.
    """
    verify_admin(current_user)
    
    # Check for $5 subscription plans
    plans = db.query(SubscriptionPlan).filter(
        (SubscriptionPlan.unit_amount_minor == 500) | 
        (SubscriptionPlan.price_usd == 5.0)
    ).all()
    
    result = {
        'plans_found': len(plans),
        'plans': [],
        'subscriptions': []
    }
    
    for plan in plans:
        result['plans'].append({
            'id': plan.id,
            'name': plan.name,
            'price_usd': plan.price_usd,
            'unit_amount_minor': plan.unit_amount_minor,
            'interval': plan.interval,
            'stripe_price_id': plan.stripe_price_id
        })
    
    # Check subscriptions
    query = db.query(UserSubscription).join(SubscriptionPlan).filter(
        (SubscriptionPlan.unit_amount_minor == 500) | 
        (SubscriptionPlan.price_usd == 5.0)
    )
    
    if user_id:
        query = query.filter(UserSubscription.user_id == user_id)
    
    subscriptions = query.all()
    
    for sub in subscriptions:
        user = db.query(User).filter(User.account_id == sub.user_id).first()
        result['subscriptions'].append({
            'user_id': sub.user_id,
            'username': user.username if user else None,
            'subscription_id': sub.id,
            'plan_id': sub.plan_id,
            'status': sub.status,
            'current_period_end': sub.current_period_end.isoformat() if sub.current_period_end else None,
            'is_active': sub.status == 'active' and (sub.current_period_end is None or sub.current_period_end > datetime.utcnow())
        })
    
    return result


@router.post("/subscriptions/five-dollar/create-plan")
async def create_five_dollar_subscription_plan(
    name: str = Body("$5 Monthly Subscription", description="Plan name"),
    stripe_price_id: Optional[str] = Body(None, description="Stripe Price ID"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Create a $5 subscription plan if it doesn't exist.
    """
    verify_admin(current_user)
    
    # Check if plan already exists
    existing = db.query(SubscriptionPlan).filter(
        (SubscriptionPlan.unit_amount_minor == 500) | 
        (SubscriptionPlan.price_usd == 5.0)
    ).first()
    
    if existing:
        return {
            'success': False,
            'message': f'$5 subscription plan already exists (ID: {existing.id})',
            'plan_id': existing.id
        }
    
    # Create new plan
    plan = SubscriptionPlan(
        name=name,
        description="$5 monthly subscription for trivia $5 mode access",
        price_usd=5.0,
        billing_interval='month',
        unit_amount_minor=500,  # $5.00 in cents
        currency='usd',
        interval='month',
        interval_count=1,
        stripe_price_id=stripe_price_id,
        livemode=False
    )
    
    db.add(plan)
    db.commit()
    db.refresh(plan)
    
    return {
        'success': True,
        'message': '$5 subscription plan created successfully',
        'plan': {
            'id': plan.id,
            'name': plan.name,
            'price_usd': plan.price_usd,
            'unit_amount_minor': plan.unit_amount_minor,
            'interval': plan.interval
        }
    }


class CreateSubscriptionRequest(BaseModel):
    user_id: Optional[int] = Field(None, description="User account ID to create subscription for. If not provided, creates for current user.")


@router.post("/subscriptions/five-dollar/create-subscription")
async def create_five_dollar_subscription_for_user(
    request: CreateSubscriptionRequest = Body(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Create an active $5 subscription for a user (for testing/admin purposes).
    This creates a UserSubscription record linking the user to the $5 plan.
    If user_id is not provided, creates subscription for the current user.
    """
    verify_admin(current_user)
    
    # If user_id not provided, use current user
    user_id = request.user_id
    if user_id is None:
        user_id = current_user.account_id
    
    # Find the $5 subscription plan
    plan = db.query(SubscriptionPlan).filter(
        (SubscriptionPlan.unit_amount_minor == 500) | 
        (SubscriptionPlan.price_usd == 5.0)
    ).first()
    
    if not plan:
        raise HTTPException(
            status_code=404,
            detail="$5 subscription plan not found. Please create it first using /admin/subscriptions/five-dollar/create-plan"
        )
    
    # Check if user exists
    user = db.query(User).filter(User.account_id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=404,
            detail=f"User with ID {user_id} not found"
        )
    
    # Check if user already has an active subscription
    existing = db.query(UserSubscription).filter(
        UserSubscription.user_id == user_id,
        UserSubscription.plan_id == plan.id,
        UserSubscription.status == 'active'
    ).first()
    
    if existing:
        return {
            'success': False,
            'message': f'User already has an active $5 subscription (ID: {existing.id})',
            'subscription_id': existing.id
        }
    
    # Create subscription (set to expire in 30 days)
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
        'message': f'Active $5 subscription created for user {user_id}',
        'subscription': {
            'id': subscription.id,
            'user_id': subscription.user_id,
            'username': user.username,
            'plan_id': subscription.plan_id,
            'plan_name': plan.name,
            'status': subscription.status,
            'current_period_end': subscription.current_period_end.isoformat() if subscription.current_period_end else None
        }
    } 