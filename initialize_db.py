"""
Script to initialize database tables and default data for the boost and gem package system.
Run this script after setting up your database connection.

Usage:
    python initialize_db.py
"""

import os
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, BoostConfig, GemPackageConfig

# Database configuration - use environment variables or defaults
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:password@localhost/trivia_pay"
)

# Create engine and session
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()


def init_db():
    """Initialize database tables and default data"""
    # Create tables if they don't exist
    Base.metadata.create_all(bind=engine)

    # Check if boost configs exist
    boost_count = db.query(BoostConfig).count()
    if boost_count == 0:
        print("Initializing default boost configurations...")
        default_boosts = [
            BoostConfig(
                boost_type="streak_saver", gems_cost=100, description="Save your streak"
            ),
            BoostConfig(
                boost_type="question_reroll",
                gems_cost=80,
                description="Change your question",
            ),
            BoostConfig(
                boost_type="extra_chance",
                gems_cost=150,
                description="Extra chance if you answer wrong",
            ),
            BoostConfig(
                boost_type="hint",
                gems_cost=30,
                description="Get a hint for the current question",
            ),
            BoostConfig(
                boost_type="fifty_fifty",
                gems_cost=50,
                description="Remove two wrong answers",
            ),
            BoostConfig(
                boost_type="change_question",
                gems_cost=10,
                description="Change to a different question",
            ),
            BoostConfig(
                boost_type="auto_submit",
                gems_cost=300,
                description="Automatically submit correct answers",
            ),
        ]

        db.add_all(default_boosts)

    # Check if gem packages exist
    gem_count = db.query(GemPackageConfig).count()
    if gem_count == 0:
        print("Initializing default gem packages...")
        default_packages = [
            GemPackageConfig(
                price_usd=0.99,
                gems_amount=500,
                is_one_time=True,
                description="One-time beginner offer",
            ),
            GemPackageConfig(
                price_usd=0.99,
                gems_amount=150,
                is_one_time=False,
                description="Basic gem pack",
            ),
            GemPackageConfig(
                price_usd=1.99,
                gems_amount=500,
                is_one_time=False,
                description="Standard gem pack",
            ),
            GemPackageConfig(
                price_usd=3.99,
                gems_amount=2400,
                is_one_time=False,
                description="Premium gem pack",
            ),
            GemPackageConfig(
                price_usd=5.99,
                gems_amount=5000,
                is_one_time=False,
                description="Super gem pack",
            ),
            GemPackageConfig(
                price_usd=9.99,
                gems_amount=12000,
                is_one_time=False,
                description="Ultimate gem pack",
            ),
        ]

        db.add_all(default_packages)

    # Commit changes
    db.commit()
    print("Database initialization complete!")


if __name__ == "__main__":
    init_db()
