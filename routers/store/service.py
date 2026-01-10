"""Store/Cosmetics service layer."""

from datetime import datetime

from fastapi import HTTPException, status

from utils.storage import presign_get

from . import repository as store_repository
from .schemas import GemPackageResponse, PurchaseResponse


def buy_gems_with_wallet(db, user, package_id: int) -> PurchaseResponse:
    gem_package = store_repository.get_gem_package_by_id(db, package_id)
    if not gem_package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Gem package with ID {package_id} not found",
        )

    price_minor = gem_package.price_minor if gem_package.price_minor is not None else 0
    price_usd_display = price_minor / 100.0

    wallet_balance_minor = (
        user.wallet_balance_minor
        if user.wallet_balance_minor is not None
        else int((user.wallet_balance or 0) * 100)
    )

    if wallet_balance_minor < price_minor:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Insufficient wallet balance. You have ${wallet_balance_minor / 100.0:.2f}, "
                f"but this package costs ${price_usd_display:.2f}"
            ),
        )

    if gem_package.is_one_time:
        existing_purchase = store_repository.get_user_gem_purchase(
            db, user_id=user.account_id, package_id=gem_package.id
        )
        if existing_purchase:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "You have already purchased this one-time offer on "
                    f"{existing_purchase.purchase_date}"
                ),
            )

    if user.wallet_balance_minor is not None:
        user.wallet_balance_minor -= price_minor
        user.wallet_balance = user.wallet_balance_minor / 100.0
    else:
        user.wallet_balance = (wallet_balance_minor - price_minor) / 100.0

    user.gems += gem_package.gems_amount
    user.last_wallet_update = datetime.utcnow()

    store_repository.create_user_gem_purchase(
        db,
        user_id=user.account_id,
        package_id=gem_package.id,
        price_paid=price_usd_display,
        gems_received=gem_package.gems_amount,
    )

    db.commit()

    remaining_balance = (
        user.wallet_balance_minor / 100.0
        if user.wallet_balance_minor is not None
        else user.wallet_balance
    )

    return PurchaseResponse(
        success=True,
        remaining_gems=user.gems,
        remaining_balance=remaining_balance,
        message=(
            f"Successfully purchased {gem_package.gems_amount} gems for "
            f"${price_usd_display:.2f}"
        ),
    )


def get_gem_packages(db):
    packages = store_repository.list_gem_packages(db)
    result = []
    for pkg in packages:
        signed_url = None
        if pkg.bucket and pkg.object_key:
            signed_url = presign_get(pkg.bucket, pkg.object_key, expires=900)

        price_minor = pkg.price_minor if pkg.price_minor is not None else 0
        price_usd_display = price_minor / 100.0 if price_minor else 0.0

        result.append(
            GemPackageResponse(
                id=pkg.id,
                price_usd=price_usd_display,
                gems_amount=pkg.gems_amount,
                is_one_time=pkg.is_one_time,
                description=pkg.description,
                url=signed_url,
                mime_type=pkg.mime_type,
                created_at=pkg.created_at,
                updated_at=pkg.updated_at,
            )
        )
    return result
