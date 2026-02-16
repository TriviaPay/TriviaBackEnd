"""
Script to check if $5 subscription plan exists and user subscriptions.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime

from core.db import SessionLocal
from models import SubscriptionPlan, User, UserSubscription


def check_five_dollar_subscription():
    """Check $5 subscription plan and user subscriptions."""
    db = SessionLocal()

    try:
        # Check for $5 subscription plans
        print("Checking for $5 subscription plans...")
        plans = (
            db.query(SubscriptionPlan)
            .filter(
                (SubscriptionPlan.unit_amount_minor == 500)
                | (SubscriptionPlan.price_usd == 5.0)
            )
            .all()
        )

        if not plans:
            print("❌ No $5 subscription plan found in database!")
            print("\nYou need to create a subscription plan with:")
            print("  - unit_amount_minor = 500 (or price_usd = 5.0)")
            print("  - interval = 'month'")
            print("  - currency = 'usd'")
            return

        print(f"✅ Found {len(plans)} $5 subscription plan(s):")
        for plan in plans:
            print(f"   Plan ID: {plan.id}")
            print(f"   Name: {plan.name}")
            print(f"   Price USD: {plan.price_usd}")
            print(f"   Unit Amount Minor: {plan.unit_amount_minor}")
            print(f"   Interval: {plan.interval}")
            print()

        # Check for active subscriptions
        print("Checking for active $5 subscriptions...")
        active_subs = (
            db.query(UserSubscription)
            .join(SubscriptionPlan)
            .filter(
                (SubscriptionPlan.unit_amount_minor == 500)
                | (SubscriptionPlan.price_usd == 5.0),
                UserSubscription.status == "active",
                UserSubscription.current_period_end > datetime.utcnow(),
            )
            .all()
        )

        print(f"✅ Found {len(active_subs)} active $5 subscription(s):")
        for sub in active_subs:
            user = db.query(User).filter(User.account_id == sub.user_id).first()
            print(
                f"   User: {user.username if user else sub.user_id} (ID: {sub.user_id})"
            )
            print(f"   Status: {sub.status}")
            print(f"   Period End: {sub.current_period_end}")
            print()

        # Check all $5 subscriptions (including inactive)
        all_subs = (
            db.query(UserSubscription)
            .join(SubscriptionPlan)
            .filter(
                (SubscriptionPlan.unit_amount_minor == 500)
                | (SubscriptionPlan.price_usd == 5.0)
            )
            .all()
        )

        print(f"Total $5 subscriptions (all statuses): {len(all_subs)}")
        if len(all_subs) > len(active_subs):
            print(f"   ({len(all_subs) - len(active_subs)} inactive/expired)")

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback

        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    check_five_dollar_subscription()
