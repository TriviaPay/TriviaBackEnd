"""
Script to initialize the $5 mode configuration in the database.
Run this script to create the five_dollar_mode config if it doesn't exist.
"""

import json
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from db import SessionLocal
from models import TriviaModeConfig


def init_five_dollar_mode():
    """Initialize $5 mode configuration."""
    db = SessionLocal()

    try:
        # Check if mode already exists
        existing = (
            db.query(TriviaModeConfig)
            .filter(TriviaModeConfig.mode_id == "five_dollar_mode")
            .first()
        )

        if existing:
            print("✅ $5 mode configuration already exists")
            print(f"   Mode ID: {existing.mode_id}")
            print(f"   Mode Name: {existing.mode_name}")
            return

        # Create $5 mode configuration
        reward_distribution = {
            "reward_type": "money",
            "distribution_method": "harmonic_sum",
            "requires_subscription": True,
            "subscription_amount": 5.0,
            "profit_share_percentage": 0.5,  # 50% of pool goes to winners
        }

        mode_config = TriviaModeConfig(
            mode_id="five_dollar_mode",
            mode_name="$5 Mode - First-Come Reward",
            questions_count=1,  # One question per day
            reward_distribution=json.dumps(reward_distribution),
            amount=5.0,  # $5 subscription required
            leaderboard_types=json.dumps(["daily"]),  # Daily leaderboard only
            ad_config=json.dumps({}),  # No ads for paid mode
            survey_config=json.dumps({}),  # No surveys for paid mode
        )

        db.add(mode_config)
        db.commit()
        db.refresh(mode_config)

        print("✅ Successfully created $5 mode configuration!")
        print(f"   Mode ID: {mode_config.mode_id}")
        print(f"   Mode Name: {mode_config.mode_name}")
        print(f"   Questions Count: {mode_config.questions_count}")
        print(f"   Amount: ${mode_config.amount}")
        print(f"   Reward Distribution: {mode_config.reward_distribution}")

    except Exception as e:
        db.rollback()
        print(f"❌ Error creating $5 mode configuration: {str(e)}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    init_five_dollar_mode()
