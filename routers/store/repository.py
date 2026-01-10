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
