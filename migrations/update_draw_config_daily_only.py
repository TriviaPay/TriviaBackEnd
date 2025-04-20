import os
import sys
import logging
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Float, DateTime, Date, UniqueConstraint, ForeignKey, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from dotenv import load_dotenv
from sqlalchemy import inspect

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables
load_dotenv()

# Database connection
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.error("DATABASE_URL environment variable is not set")
    sys.exit(1)

# Convert postgres:// to postgresql:// for SQLAlchemy 1.4+
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Create SQLAlchemy engine and session
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Define models for migration (minimal versions)
class TriviaDrawConfig(Base):
    __tablename__ = "trivia_draw_config"
    
    id = Column(Integer, primary_key=True, index=True)
    is_custom = Column(Boolean, default=False)  
    custom_winner_count = Column(Integer, nullable=True)  
    daily_pool_amount = Column(Float, default=100.0)  # New column
    daily_winners_count = Column(Integer, default=3)  # New column
    automatic_draws = Column(Boolean, default=True)   # New column
    custom_data = Column(String, nullable=True)  
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class TriviaDrawWinner(Base):
    __tablename__ = "trivia_draw_winners"
    
    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("users.account_id"), nullable=False)
    prize_amount = Column(Float, nullable=False)
    position = Column(Integer, nullable=False)  
    draw_date = Column(Date, nullable=False)  
    draw_type = Column(String, default="daily", nullable=False)  # New column
    created_at = Column(DateTime, default=datetime.utcnow)
    

def run_migration():
    """Run the migration to update the draw configuration for daily-only draws"""
    db = SessionLocal()
    try:
        logger.info("Starting migration for daily-only draws...")
        
        # 1. Check if tables exist
        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()
        if "trivia_draw_config" not in existing_tables:
            logger.error("trivia_draw_config table does not exist. Migration cannot proceed.")
            return
        
        # 2. Add new columns to trivia_draw_config if they don't exist
        columns_to_add = {
            "daily_pool_amount": "ALTER TABLE trivia_draw_config ADD COLUMN IF NOT EXISTS daily_pool_amount FLOAT DEFAULT 100.0;",
            "daily_winners_count": "ALTER TABLE trivia_draw_config ADD COLUMN IF NOT EXISTS daily_winners_count INTEGER DEFAULT 3;",
            "automatic_draws": "ALTER TABLE trivia_draw_config ADD COLUMN IF NOT EXISTS automatic_draws BOOLEAN DEFAULT TRUE;"
        }
        
        for col_name, sql in columns_to_add.items():
            try:
                logger.info(f"Adding column {col_name} to trivia_draw_config if it doesn't exist...")
                db.execute(text(sql))
                db.commit()
                logger.info(f"Column {col_name} added or already exists")
            except Exception as e:
                db.rollback()
                logger.error(f"Error adding {col_name} column: {str(e)}")
        
        # 3. Add draw_type column to trivia_draw_winners if it doesn't exist
        try:
            logger.info("Adding draw_type column to trivia_draw_winners if it doesn't exist...")
            db.execute(text("ALTER TABLE trivia_draw_winners ADD COLUMN IF NOT EXISTS draw_type VARCHAR DEFAULT 'daily' NOT NULL;"))
            db.commit()
            logger.info("Column draw_type added or already exists")
        except Exception as e:
            db.rollback()
            logger.error(f"Error adding draw_type column: {str(e)}")
        
        # 4. Update existing draw_type values to 'daily'
        try:
            logger.info("Setting all existing draw winners to draw_type='daily'...")
            db.execute(text("UPDATE trivia_draw_winners SET draw_type = 'daily' WHERE draw_type IS NULL OR draw_type = '';"))
            db.commit()
            logger.info("All existing draw winners updated to daily type")
        except Exception as e:
            db.rollback()
            logger.error(f"Error updating draw_type values: {str(e)}")
        
        # 5. Update trivia_draw_config values
        try:
            count = db.execute(text("SELECT COUNT(*) FROM trivia_draw_config")).scalar()
            if count == 0:
                logger.info("No trivia_draw_config records found. Creating default config...")
                db.execute(text("""
                INSERT INTO trivia_draw_config 
                (is_custom, custom_winner_count, daily_pool_amount, daily_winners_count, automatic_draws, created_at, updated_at)
                VALUES 
                (FALSE, NULL, 100.0, 3, TRUE, NOW(), NOW());
                """))
            else:
                logger.info("Updating existing trivia_draw_config records...")
                db.execute(text("""
                UPDATE trivia_draw_config
                SET daily_pool_amount = 100.0, daily_winners_count = 3, automatic_draws = TRUE
                WHERE daily_pool_amount IS NULL OR daily_winners_count IS NULL OR automatic_draws IS NULL;
                """))
            db.commit()
            logger.info("trivia_draw_config updated successfully")
        except Exception as e:
            db.rollback()
            logger.error(f"Error updating trivia_draw_config: {str(e)}")
        
        logger.info("Migration for daily-only draws completed successfully")
        
    except Exception as e:
        logger.error(f"Migration failed: {str(e)}")
    finally:
        db.close()

if __name__ == "__main__":
    run_migration() 