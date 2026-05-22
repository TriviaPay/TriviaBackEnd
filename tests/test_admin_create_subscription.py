from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from models import SubscriptionPlan, User, UserSubscription
from routers.auth.service import create_subscription_for_user


def _make_db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)
    for table in (
        User.__table__,
        SubscriptionPlan.__table__,
        UserSubscription.__table__,
    ):
        table.create(bind=engine)
    return engine, SessionLocal()


def _seed_user_and_plan(db):
    user = User(
        account_id=6124901903,
        descope_user_id="descope-test-user",
        email="subscriber@example.com",
        username="subscriber",
    )
    plan = SubscriptionPlan(
        id=1,
        name="Bronze",
        description="Bronze plan",
        price_usd=5.0,
        billing_interval="month",
        unit_amount_minor=500,
        currency="usd",
        interval="month",
        interval_count=1,
        livemode=False,
    )
    db.add_all([user, plan])
    db.commit()
    return user, plan


def test_create_subscription_for_user_reactivates_existing_subscription():
    engine, db = _make_db_session()
    try:
        user, plan = _seed_user_and_plan(db)
        existing = UserSubscription(
            user_id=user.account_id,
            plan_id=plan.id,
            status="canceled",
            current_period_start=datetime.utcnow() - timedelta(days=60),
            current_period_end=datetime.utcnow() - timedelta(days=30),
            cancel_at_period_end=True,
            cancel_at=datetime.utcnow() - timedelta(days=31),
            canceled_at=datetime.utcnow() - timedelta(days=30),
            pause_collection="void",
            livemode=True,
        )
        db.add(existing)
        db.commit()

        response = create_subscription_for_user(
            db,
            SimpleNamespace(user_id=user.account_id, plan_id=plan.id),
            user,
        )

        refreshed = db.query(UserSubscription).filter_by(id=existing.id).one()
        assert response["success"] is True
        assert response["message"] == f"Active subscription reactivated for user {user.account_id}"
        assert response["subscription"]["id"] == existing.id
        assert refreshed.status == "active"
        assert refreshed.cancel_at_period_end is False
        assert refreshed.cancel_at is None
        assert refreshed.canceled_at is None
        assert refreshed.pause_collection is None
        assert refreshed.livemode is False
        assert refreshed.current_period_end > refreshed.current_period_start
        assert db.query(UserSubscription).count() == 1
    finally:
        db.close()
        for table in (
            UserSubscription.__table__,
            SubscriptionPlan.__table__,
            User.__table__,
        ):
            table.drop(bind=engine)
        engine.dispose()


def test_create_subscription_for_user_returns_existing_active_subscription():
    engine, db = _make_db_session()
    try:
        user, plan = _seed_user_and_plan(db)
        existing = UserSubscription(
            user_id=user.account_id,
            plan_id=plan.id,
            status="active",
            current_period_start=datetime.utcnow(),
            current_period_end=datetime.utcnow() + timedelta(days=7),
            livemode=False,
        )
        db.add(existing)
        db.commit()

        response = create_subscription_for_user(
            db,
            SimpleNamespace(user_id=user.account_id, plan_id=plan.id),
            user,
        )

        assert response["success"] is False
        assert response["subscription_id"] == existing.id
        assert db.query(UserSubscription).count() == 1
    finally:
        db.close()
        for table in (
            UserSubscription.__table__,
            SubscriptionPlan.__table__,
            User.__table__,
        ):
            table.drop(bind=engine)
        engine.dispose()
