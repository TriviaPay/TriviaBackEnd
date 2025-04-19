"""
Script to check avatar and frame integrity and send notifications to admin users.
Can be run as a scheduled task/cron job.

Run with: python scripts/check_cosmetics_integrity.py
"""

import os
import sys
from datetime import datetime

# Add parent directory to path to allow imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from models import User, Avatar, Frame, UserAvatar, UserFrame, Notification
from db import get_db
import logging
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

# Load environment variables for email settings
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('cosmetics_integrity.log')
    ]
)

logger = logging.getLogger(__name__)

# Email configuration
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")

def check_avatar_integrity(db: Session) -> dict:
    """
    Check the integrity of avatar selections
    """
    logger.info("Checking avatar integrity")
    
    # Get all users with a selected avatar
    users_with_avatars = db.query(User).filter(User.selected_avatar_id != None).all()
    
    # Get list of all available avatar IDs
    all_avatars = {avatar.id: avatar for avatar in db.query(Avatar).all()}
    
    # Track statistics
    total_users = len(users_with_avatars)
    valid_count = 0
    invalid_count = 0
    invalid_details = []
    
    for user in users_with_avatars:
        # Check if selected avatar exists
        if user.selected_avatar_id not in all_avatars:
            invalid_count += 1
            
            # Record details of invalid selection
            invalid_details.append({
                "user_id": user.account_id,
                "username": user.username,
                "avatar_id": user.selected_avatar_id,
                "owned_avatars_count": db.query(UserAvatar).filter(UserAvatar.user_id == user.account_id).count()
            })
        else:
            valid_count += 1
    
    return {
        "total_users": total_users,
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "invalid_details": invalid_details
    }

def check_frame_integrity(db: Session) -> dict:
    """
    Check the integrity of frame selections
    """
    logger.info("Checking frame integrity")
    
    # Get all users with a selected frame
    users_with_frames = db.query(User).filter(User.selected_frame_id != None).all()
    
    # Get list of all available frame IDs
    all_frames = {frame.id: frame for frame in db.query(Frame).all()}
    
    # Track statistics
    total_users = len(users_with_frames)
    valid_count = 0
    invalid_count = 0
    invalid_details = []
    
    for user in users_with_frames:
        # Check if selected frame exists
        if user.selected_frame_id not in all_frames:
            invalid_count += 1
            
            # Record details of invalid selection
            invalid_details.append({
                "user_id": user.account_id,
                "username": user.username,
                "frame_id": user.selected_frame_id,
                "owned_frames_count": db.query(UserFrame).filter(UserFrame.user_id == user.account_id).count()
            })
        else:
            valid_count += 1
    
    return {
        "total_users": total_users,
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "invalid_details": invalid_details
    }

def send_notification_email(report: dict):
    """
    Send notification email to admin
    """
    if not EMAIL_USER or not EMAIL_PASSWORD or not ADMIN_EMAIL:
        logger.warning("Email credentials not configured. Skipping email notification.")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_USER
        msg['To'] = ADMIN_EMAIL
        msg['Subject'] = f"TriviaPay Cosmetics Integrity Report - {datetime.now().strftime('%Y-%m-%d')}"
        
        body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; }}
                .summary {{ background-color: #f5f5f5; padding: 10px; margin-bottom: 20px; }}
                .warning {{ color: red; font-weight: bold; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #f2f2f2; }}
                tr:nth-child(even) {{ background-color: #f9f9f9; }}
            </style>
        </head>
        <body>
            <h2>TriviaPay Cosmetics Integrity Report</h2>
            <p>Report generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            
            <div class="summary">
                <h3>Summary</h3>
                <p>Avatars: {report['avatar']['total_users']} total, {report['avatar']['invalid_count']} invalid</p>
                <p>Frames: {report['frame']['total_users']} total, {report['frame']['invalid_count']} invalid</p>
                
                {'<p class="warning">⚠️ There are inconsistencies in the database that should be fixed.</p>' 
                  if report['avatar']['invalid_count'] > 0 or report['frame']['invalid_count'] > 0 
                  else '<p>✅ No issues found.</p>'}
            </div>
        """
        
        if report['avatar']['invalid_count'] > 0:
            body += f"""
            <h3>Invalid Avatars</h3>
            <table>
                <tr>
                    <th>User ID</th>
                    <th>Username</th>
                    <th>Invalid Avatar ID</th>
                    <th>Owned Avatars</th>
                </tr>
            """
            
            for detail in report['avatar']['invalid_details'][:20]:  # Limit to 20 entries
                body += f"""
                <tr>
                    <td>{detail['user_id']}</td>
                    <td>{detail['username'] or 'N/A'}</td>
                    <td>{detail['avatar_id']}</td>
                    <td>{detail['owned_avatars_count']}</td>
                </tr>
                """
            
            body += "</table>"
        
        if report['frame']['invalid_count'] > 0:
            body += f"""
            <h3>Invalid Frames</h3>
            <table>
                <tr>
                    <th>User ID</th>
                    <th>Username</th>
                    <th>Invalid Frame ID</th>
                    <th>Owned Frames</th>
                </tr>
            """
            
            for detail in report['frame']['invalid_details'][:20]:  # Limit to 20 entries
                body += f"""
                <tr>
                    <td>{detail['user_id']}</td>
                    <td>{detail['username'] or 'N/A'}</td>
                    <td>{detail['frame_id']}</td>
                    <td>{detail['owned_frames_count']}</td>
                </tr>
                """
            
            body += "</table>"
        
        body += f"""
            <p>To fix these issues, use the admin endpoints:</p>
            <ul>
                <li><code>GET /admin/db-integrity/avatars?fix=true</code> - Fix avatar inconsistencies</li>
                <li><code>GET /admin/db-integrity/frames?fix=true</code> - Fix frame inconsistencies</li>
            </ul>
            
            <p>Or run the verification scripts:</p>
            <ul>
                <li><code>python scripts/verify_avatars.py</code></li>
                <li><code>python scripts/verify_frames.py</code></li>
            </ul>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(body, 'html'))
        
        server = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        
        logger.info(f"Notification email sent to {ADMIN_EMAIL}")
        return True
    except Exception as e:
        logger.error(f"Error sending notification email: {str(e)}", exc_info=True)
        return False

def create_in_app_notification(db: Session, report: dict):
    """
    Create in-app notifications for admins
    """
    try:
        # Check if User model has is_admin field
        if hasattr(User, 'is_admin'):
            admin_users = db.query(User).filter(User.is_admin == True).all()
        else:
            # Try to find admin users by other means or use a specific account
            # For example, looking for specific username or email pattern
            admin_users = db.query(User).filter(User.email.like('%admin%')).all()
            
            # If no admin users found, log a warning
            if not admin_users:
                logger.warning("No admin users found by email pattern. Check if admin users are designated differently.")
                return
        
        if not admin_users:
            logger.warning("No admin users found for in-app notifications")
            return
        
        # Create notification content
        if report['avatar']['invalid_count'] > 0 or report['frame']['invalid_count'] > 0:
            message = (
                f"Cosmetics integrity check found issues: "
                f"{report['avatar']['invalid_count']} invalid avatars, "
                f"{report['frame']['invalid_count']} invalid frames. "
                f"Please check admin dashboard."
            )
            
            for admin in admin_users:
                notification = Notification(
                    account_id=admin.account_id,
                    notification_type="integrity_check",
                    message=message,
                    is_read=False,
                    created_at=datetime.utcnow()
                )
                db.add(notification)
            
            db.commit()
            logger.info(f"Created in-app notifications for {len(admin_users)} admin users")
        else:
            logger.info("No issues found, no admin notifications created")
    
    except Exception as e:
        logger.error(f"Error creating in-app notifications: {str(e)}", exc_info=True)
        db.rollback()

def main():
    """
    Main function to check cosmetics integrity and send notifications
    """
    print("Starting cosmetics integrity check...")
    db = next(get_db())
    
    # Check integrity
    avatar_report = check_avatar_integrity(db)
    frame_report = check_frame_integrity(db)
    
    # Create full report
    report = {
        "timestamp": datetime.now().isoformat(),
        "avatar": avatar_report,
        "frame": frame_report
    }
    
    # Print summary
    print(f"Avatar check: {avatar_report['total_users']} total, {avatar_report['invalid_count']} invalid")
    print(f"Frame check: {frame_report['total_users']} total, {frame_report['invalid_count']} invalid")
    
    # Save report to file
    with open(f"cosmetics_integrity_{datetime.now().strftime('%Y%m%d')}.json", "w") as f:
        json.dump(report, f, indent=2)
    
    # Create in-app notifications for admins
    try:
        # Check if the Notification class is defined (not all installations may have it)
        if 'Notification' in globals():
            create_in_app_notification(db, report)
        else:
            logger.warning("Notification class not defined, skipping in-app notifications")
    except Exception as e:
        logger.error(f"Error with in-app notifications: {str(e)}", exc_info=True)
    
    # Send email notification if there are issues
    if avatar_report['invalid_count'] > 0 or frame_report['invalid_count'] > 0:
        print("Issues found, sending notification email...")
        send_notification_email(report)
    
    print("Integrity check completed.")
    return report

if __name__ == "__main__":
    main() 