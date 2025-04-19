from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func, text, desc
import pytz
from db import get_db
from models import Winner, User, TriviaDrawWinner, TriviaDrawConfig, Badge, Avatar, Frame
from routers.dependencies import get_current_user
from pydantic import BaseModel
import logging

# Configure logger
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/winners", tags=["Winners"])

# === Pydantic Models ===
class WinnerResponse(BaseModel):
    username: str
    amount_won: float
    total_amount_won: float = 0
    badge_name: Optional[str] = None
    badge_image_url: Optional[str] = None
    avatar_url: Optional[str] = None
    frame_url: Optional[str] = None
    position: int
    draw_date: Optional[str] = None  # Date on which the user won

@router.get("/")
def get_recent_winners(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)  # Protect this route
):
    """
    Endpoint to fetch recent winners. Only accessible if you have a valid Auth0 token.
    Fetches up to 5 most recent winners from the database.
    """
    winners = (
        db.query(Winner)
          .order_by(Winner.win_date.desc())
          .limit(5)
          .all()
    )

    return {
        "winners": [
            {
                "account_id": w.account_id,
                "amount_won": w.amount_won,
                "win_date": w.win_date,
                "profile_pic_url": w.user.profile_pic_url if w.user else None,
            }
            for w in winners
        ]
    }

@router.get("/daily", 
    response_model=List[WinnerResponse],
    summary="Get daily winners with detailed information",
    description="""
    Retrieves detailed information about winners for a specific date's draw.
    
    If a date is provided via the 'date_str' query parameter (in YYYY-MM-DD format),
    the endpoint returns winners for that particular date.
    
    If no date is provided, it defaults to returning yesterday's winners.
    
    The response includes comprehensive user information including username,
    amount won in this specific draw, total amount won all-time across all draws,
    profile customizations (badge, avatar, frame), position in the draw,
    and the date on which they won.
    
    This endpoint is protected and requires user authentication.
    """,
    responses={
        200: {
            "description": "List of daily winners retrieved successfully",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "username": "winner1",
                            "amount_won": 250.0,
                            "total_amount_won": 1250.0,
                            "badge_name": "Gold",
                            "badge_image_url": "https://example.com/gold.png",
                            "avatar_url": "https://example.com/avatar.png",
                            "frame_url": "https://example.com/frame.png",
                            "position": 1,
                            "draw_date": "2023-06-14"
                        }
                    ]
                }
            }
        },
        401: {
            "description": "Unauthorized - Authentication token is missing or invalid"
        },
        500: {
            "description": "Internal server error occurred while retrieving winners",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Error retrieving daily winners: [error details]"
                    }
                }
            }
        }
    }
)
async def get_daily_winners(
    date_str: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Retrieves the daily winners with detailed information.
    
    If no date is provided, it will return yesterday's winners.
    """
    try:
        logger.info(f"get_daily_winners called with date_str={date_str}")
        
        if date_str:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        else:
            # Default to yesterday if no date specified
            est = pytz.timezone('US/Eastern')
            target_date = (datetime.now(est) - timedelta(days=1)).date()

        logger.info(f"Target date for winners: {target_date}")

        # Get winners for the specified date
        winners_query = db.query(TriviaDrawWinner, User).join(
            User, TriviaDrawWinner.account_id == User.account_id
        ).filter(
            TriviaDrawWinner.draw_date == target_date
        ).order_by(TriviaDrawWinner.position).all()
        
        logger.info(f"Found {len(winners_query)} winners for date {target_date}")

        result = []
        
        for winner, user in winners_query:
            try:
                # Calculate total amount won by user all-time
                total_won = db.query(func.sum(TriviaDrawWinner.prize_amount)).filter(
                    TriviaDrawWinner.account_id == user.account_id
                ).scalar() or 0
                
                # Get badge information
                badge_name = None
                badge_image_url = None
                if user.badge_info:
                    badge_name = user.badge_info.name
                    badge_image_url = user.badge_image_url
                
                # Get avatar URL
                avatar_url = None
                if user.selected_avatar_id:
                    avatar_query = text("""
                        SELECT image_url FROM avatars 
                        WHERE id = :avatar_id
                    """)
                    avatar_result = db.execute(avatar_query, {"avatar_id": user.selected_avatar_id}).first()
                    if avatar_result:
                        avatar_url = avatar_result[0]
                
                # Get frame URL
                frame_url = None
                if user.selected_frame_id:
                    frame_query = text("""
                        SELECT image_url FROM frames 
                        WHERE id = :frame_id
                    """)
                    frame_result = db.execute(frame_query, {"frame_id": user.selected_frame_id}).first()
                    if frame_result:
                        frame_url = frame_result[0]
                
                result.append(WinnerResponse(
                    username=user.username or f"User{user.account_id}",
                    amount_won=winner.prize_amount,
                    total_amount_won=total_won,
                    badge_name=badge_name,
                    badge_image_url=badge_image_url,
                    avatar_url=avatar_url,
                    frame_url=frame_url,
                    position=winner.position,
                    draw_date=winner.draw_date.isoformat() if winner.draw_date else None
                ))
            except Exception as user_error:
                logger.error(f"Error processing winner {user.account_id}: {str(user_error)}")
        
        return result
        
    except Exception as e:
        logger.error(f"Error in get_daily_winners: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving daily winners: {str(e)}"
        )

@router.get("/weekly-winners", 
    response_model=List[WinnerResponse],
    summary="Get weekly aggregated winners list",
    description="""
    Retrieves an aggregated list of winners for the specified week.
    
    If a date is provided via the 'date_str' query parameter (in YYYY-MM-DD format),
    the endpoint returns winners for the week containing that date.
    
    If no date is provided, it defaults to the current week's winners.
    
    The winners are aggregated across the entire week, and the response includes
    detailed user information such as username, total amount won during the week,
    all-time winnings, profile customizations (badge, avatar, frame), ranking position,
    and the week end date.
    
    This endpoint is protected and requires user authentication.
    """,
    responses={
        200: {
            "description": "List of weekly aggregated winners retrieved successfully",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "username": "topwinner",
                            "amount_won": 750.0,
                            "total_amount_won": 2500.0,
                            "badge_name": "Diamond",
                            "badge_image_url": "https://example.com/diamond.png",
                            "avatar_url": "https://example.com/avatar.png",
                            "frame_url": "https://example.com/frame.png",
                            "position": 1,
                            "draw_date": "2023-06-18"  # Week end date
                        }
                    ]
                }
            }
        },
        401: {
            "description": "Unauthorized - Authentication token is missing or invalid"
        },
        500: {
            "description": "Internal server error occurred while retrieving weekly winners",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Error retrieving weekly winners: [error details]"
                    }
                }
            }
        }
    }
)
async def get_weekly_winners(
    date_str: Optional[str] = None, 
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Get the list of weekly winners aggregated by account.
    If no date is provided, returns the current week's winners.
    """
    try:
        # Get current date in EST
        est = pytz.timezone('US/Eastern')
        today = datetime.now(est).date()
        
        # Calculate start of the week (Monday)
        start_of_week = today - timedelta(days=today.weekday())
        
        # Calculate end of the week (Sunday)
        end_of_week = start_of_week + timedelta(days=6)
        
        # Get weekly winners (aggregated by account_id)
        winners_query = text("""
            SELECT 
                dw.account_id, 
                SUM(dw.prize_amount) as weekly_amount,
                MIN(dw.position) as best_position,
                MAX(dw.draw_date) as latest_draw_date
            FROM trivia_draw_winners dw
            WHERE dw.draw_date BETWEEN :start_date AND :end_date
            GROUP BY dw.account_id
            ORDER BY weekly_amount DESC
            LIMIT 50
        """)
        
        winners_result = db.execute(winners_query, {
            "start_date": start_of_week,
            "end_date": end_of_week
        }).fetchall()
        
        # Format the response
        result = []
        position = 1
        
        for winner_data in winners_result:
            account_id, weekly_amount, best_position, latest_draw_date = winner_data
            
            # Get user details
            user = db.query(User).filter(User.account_id == account_id).first()
            if not user:
                continue
            
            # Calculate total amount won by user all-time
            total_won = db.query(func.sum(TriviaDrawWinner.prize_amount)).filter(
                TriviaDrawWinner.account_id == user.account_id
            ).scalar() or 0
            
            # Get badge information
            badge_name = None
            badge_image_url = None
            if user.badge_info:
                badge_name = user.badge_info.name
                badge_image_url = user.badge_image_url
            
            # Get avatar URL
            avatar_url = None
            if user.selected_avatar_id:
                avatar_query = text("""
                    SELECT image_url FROM avatars 
                    WHERE id = :avatar_id
                """)
                avatar_result = db.execute(avatar_query, {"avatar_id": user.selected_avatar_id}).first()
                if avatar_result:
                    avatar_url = avatar_result[0]
            
            # Get frame URL
            frame_url = None
            if user.selected_frame_id:
                frame_query = text("""
                    SELECT image_url FROM frames 
                    WHERE id = :frame_id
                """)
                frame_result = db.execute(frame_query, {"frame_id": user.selected_frame_id}).first()
                if frame_result:
                    frame_url = frame_result[0]
            
            result.append(WinnerResponse(
                username=user.username or f"User{user.account_id}",
                amount_won=weekly_amount,
                total_amount_won=total_won,
                badge_name=badge_name,
                badge_image_url=badge_image_url,
                avatar_url=avatar_url,
                frame_url=frame_url,
                position=position,
                draw_date=latest_draw_date.isoformat() if latest_draw_date else None
            ))
            
            position += 1
        
        return result
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving weekly winners: {str(e)}"
        )

@router.get("/all-time-winners", 
    response_model=List[WinnerResponse],
    summary="Get all-time top winners",
    description="""
    Retrieves a list of users who have won the most rewards across all draws.
    
    This endpoint provides an aggregated view of the highest-earning users on the platform,
    sorted by their total winnings in descending order (highest winners first).
    
    The response includes detailed information for each winner such as their username,
    total amount won across all draws, profile customizations (badge, avatar, frame),
    and their ranking position.
    
    This endpoint is protected and requires user authentication.
    """,
    responses={
        200: {
            "description": "List of all-time winners retrieved successfully",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "username": "legendwinner",
                            "amount_won": 0.0,  # Not used for all-time winners
                            "total_amount_won": 5000.0,
                            "badge_name": "Platinum",
                            "badge_image_url": "https://example.com/platinum.png",
                            "avatar_url": "https://example.com/avatar.png",
                            "frame_url": "https://example.com/frame.png",
                            "position": 1,
                            "draw_date": None  # Not applicable for all-time winners
                        }
                    ]
                }
            }
        },
        401: {
            "description": "Unauthorized - Authentication token is missing or invalid"
        },
        500: {
            "description": "Internal server error occurred while retrieving all-time winners",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "Error retrieving all-time winners: [error details]"
                    }
                }
            }
        }
    }
)
async def get_all_time_winners(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Get the list of users who have won the most rewards across all draws.
    Returns an ordered list of the highest-earning users.
    """
    try:
        # Get all-time winners (aggregated by account_id)
        winners_query = text("""
            SELECT 
                dw.account_id, 
                SUM(dw.prize_amount) as total_amount,
                MIN(dw.position) as best_position,
                MAX(dw.draw_date) as latest_draw_date
            FROM trivia_draw_winners dw
            GROUP BY dw.account_id
            ORDER BY total_amount DESC
            LIMIT 50
        """)
        
        winners_result = db.execute(winners_query).fetchall()
        
        # Format the response
        result = []
        position = 1
        
        for winner_data in winners_result:
            account_id, total_amount, best_position, latest_draw_date = winner_data
            
            # Get user details
            user = db.query(User).filter(User.account_id == account_id).first()
            if not user:
                continue
            
            # Get badge information
            badge_name = None
            badge_image_url = None
            if user.badge_info:
                badge_name = user.badge_info.name
                badge_image_url = user.badge_image_url
            
            # Get avatar URL
            avatar_url = None
            if user.selected_avatar_id:
                avatar_query = text("""
                    SELECT image_url FROM avatars 
                    WHERE id = :avatar_id
                """)
                avatar_result = db.execute(avatar_query, {"avatar_id": user.selected_avatar_id}).first()
                if avatar_result:
                    avatar_url = avatar_result[0]
            
            # Get frame URL
            frame_url = None
            if user.selected_frame_id:
                frame_query = text("""
                    SELECT image_url FROM frames 
                    WHERE id = :frame_id
                """)
                frame_result = db.execute(frame_query, {"frame_id": user.selected_frame_id}).first()
                if frame_result:
                    frame_url = frame_result[0]
            
            result.append(WinnerResponse(
                username=user.username or f"User{user.account_id}",
                amount_won=total_amount,
                total_amount_won=total_amount,
                badge_name=badge_name,
                badge_image_url=badge_image_url,
                avatar_url=avatar_url,
                frame_url=frame_url,
                position=position,
                draw_date=latest_draw_date.isoformat() if latest_draw_date else None
            ))
            
            position += 1
        
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving all-time winners: {str(e)}"
        )

@router.get("/streaks", response_model=List[Dict[str, Any]])
async def get_all_user_streaks(
    skip: int = Query(0, description="Number of records to skip for pagination"),
    limit: int = Query(50, description="Maximum number of records to return"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    Get all users' streaks with their profile details.
    
    Results are ordered by:
    1. Streak count (descending)
    2. Last streak update time (oldest first if streak count is tied)
    
    This endpoint is available to all authenticated users.
    """
    logger.info(f"Getting all user streaks, skip={skip}, limit={limit}")
    
    try:
        # Fetch users with their streaks and profile details
        query = (
            db.query(
                User.account_id,
                User.username,
                User.streaks,
                User.badge_image_url,
                User.last_streak_date,
                User.selected_avatar_id,
                User.selected_frame_id,
                User.profile_pic_url,
                Avatar.image_url.label("avatar_url"),
                Frame.image_url.label("frame_url"),
                Badge.name.label("badge_name"),
                Badge.image_url.label("badge_url"),
            )
            .outerjoin(Avatar, User.selected_avatar_id == Avatar.id)
            .outerjoin(Frame, User.selected_frame_id == Frame.id)
            .outerjoin(Badge, User.badge_id == Badge.id)
            .filter(User.streaks > 0)  # Only users with streak > 0
            .order_by(
                User.streaks.desc(),  # Primary sort by streak (descending)
                User.last_streak_date.asc().nullslast()  # Secondary sort by last update (oldest first)
            )
        )
        
        # Apply pagination
        user_streaks = query.offset(skip).limit(limit).all()
        
        # Format the results
        result = []
        for user in user_streaks:
            display_image = user.avatar_url if user.selected_avatar_id else user.profile_pic_url
            
            result.append({
                "account_id": user.account_id,
                "username": user.username or f"User{user.account_id}",  # Fallback for users without username
                "streaks": user.streaks,
                "last_streak_date": user.last_streak_date.isoformat() if user.last_streak_date else None,
                "display_image": display_image,
                "frame_url": user.frame_url,
                "badge_name": user.badge_name,
                "badge_url": user.badge_url or user.badge_image_url,  # Use cached badge image as fallback
            })
        
        logger.info(f"Returning {len(result)} user streaks")
        return result
        
    except Exception as e:
        logger.error(f"Error retrieving user streaks: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving user streaks: {str(e)}"
        )
