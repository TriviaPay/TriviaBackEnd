"""Store/Cosmetics repository layer."""

from sqlalchemy.orm import Session


def get_gem_package_by_id(db: Session, package_id: int):
    from models import GemPackageConfig

    return db.query(GemPackageConfig).filter(GemPackageConfig.id == package_id).first()


def list_gem_packages(db: Session):
    from models import GemPackageConfig

    return db.query(GemPackageConfig).all()


def get_user_gem_purchase(db: Session, user_id: int, package_id: int):
    from models import UserGemPurchase

    return (
        db.query(UserGemPurchase)
        .filter(
            UserGemPurchase.user_id == user_id, UserGemPurchase.package_id == package_id
        )
        .first()
    )


def create_user_gem_purchase(
    db: Session, *, user_id: int, package_id: int, price_paid: float, gems_received: int
):
    from models import UserGemPurchase

    purchase_record = UserGemPurchase(
        user_id=user_id,
        package_id=package_id,
        price_paid=price_paid,
        gems_received=gems_received,
    )
    db.add(purchase_record)
    return purchase_record


# --- Cosmetics ---


def list_avatars(db: Session, *, skip: int, limit: int):
    from sqlalchemy import desc

    from models import Avatar

    return (
        db.query(Avatar)
        .order_by(desc(Avatar.created_at))
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_avatar(db: Session, *, avatar_id: str):
    from models import Avatar

    return db.query(Avatar).filter(Avatar.id == avatar_id).first()


def get_user_avatar_ownership(db: Session, *, user_id: int, avatar_id: str):
    from models import UserAvatar

    return (
        db.query(UserAvatar)
        .filter(UserAvatar.user_id == user_id, UserAvatar.avatar_id == avatar_id)
        .first()
    )


def create_user_avatar_ownership(db: Session, *, user_id: int, avatar_id: str, purchase_date):
    from models import UserAvatar

    ownership = UserAvatar(user_id=user_id, avatar_id=avatar_id, purchase_date=purchase_date)
    db.add(ownership)
    return ownership


def lock_user(db: Session, *, user_id: int):
    from core.users import get_user_by_id_for_update

    return get_user_by_id_for_update(db, account_id=user_id)


def list_user_owned_avatars(db: Session, *, user_id: int):
    from sqlalchemy import desc

    from models import Avatar, UserAvatar

    return (
        db.query(Avatar, UserAvatar.purchase_date)
        .join(UserAvatar, UserAvatar.avatar_id == Avatar.id)
        .filter(UserAvatar.user_id == user_id)
        .order_by(desc(UserAvatar.purchase_date))
        .all()
    )


def list_frames(db: Session, *, skip: int, limit: int):
    from sqlalchemy import desc

    from models import Frame

    return (
        db.query(Frame)
        .order_by(desc(Frame.created_at))
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_frame(db: Session, *, frame_id: str):
    from models import Frame

    return db.query(Frame).filter(Frame.id == frame_id).first()


def get_user_frame_ownership(db: Session, *, user_id: int, frame_id: str):
    from models import UserFrame

    return (
        db.query(UserFrame)
        .filter(UserFrame.user_id == user_id, UserFrame.frame_id == frame_id)
        .first()
    )


def create_user_frame_ownership(db: Session, *, user_id: int, frame_id: str, purchase_date):
    from models import UserFrame

    ownership = UserFrame(user_id=user_id, frame_id=frame_id, purchase_date=purchase_date)
    db.add(ownership)
    return ownership


def list_user_owned_frames(db: Session, *, user_id: int):
    from sqlalchemy import desc

    from models import Frame, UserFrame

    return (
        db.query(Frame, UserFrame.purchase_date)
        .join(UserFrame, UserFrame.frame_id == Frame.id)
        .filter(UserFrame.user_id == user_id)
        .order_by(desc(UserFrame.purchase_date))
        .all()
    )
