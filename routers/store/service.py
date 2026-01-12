"""Store/Cosmetics service layer."""

from datetime import datetime

from fastapi import HTTPException, status

from core.cache import default_cache
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
    def _build():
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

    return default_cache.get_or_set("store:gem_packages:v1", ttl_seconds=60, factory=_build)


def list_avatars(db, *, current_user, skip: int, limit: int, include_urls: bool):
    key = f"store:avatars:v1:skip={skip}:limit={limit}:urls={int(bool(include_urls))}"

    def _build():
        avatars = store_repository.list_avatars(db, skip=skip, limit=limit)
        out = []
        presign_cache = {}
        for av in avatars:
            signed = None
            bucket = getattr(av, "bucket", None)
            object_key = getattr(av, "object_key", None)
            if include_urls and bucket and object_key:
                cache_key = (bucket, object_key)
                if cache_key in presign_cache:
                    signed = presign_cache[cache_key]
                else:
                    signed = presign_get(bucket, object_key, expires=900)
                    presign_cache[cache_key] = signed

            out.append(
                {
                    "id": av.id,
                    "name": av.name,
                    "description": av.description,
                    "price_gems": av.price_gems,
                    "price_minor": av.price_minor,
                    "price_usd": getattr(av, "price_usd", None),
                    "is_premium": av.is_premium,
                    "bucket": getattr(av, "bucket", None),
                    "object_key": getattr(av, "object_key", None),
                    "mime_type": getattr(av, "mime_type", None),
                    "created_at": av.created_at,
                    "url": signed,
                }
            )
        return out

    return default_cache.get_or_set(key, ttl_seconds=60, factory=_build)


def list_owned_avatars(db, *, current_user, include_urls: bool):
    rows = store_repository.list_user_owned_avatars(db, user_id=current_user.account_id)
    out = []
    presign_cache = {}
    for av, purchased_at in rows:
        signed = None
        bucket = getattr(av, "bucket", None)
        object_key = getattr(av, "object_key", None)
        if include_urls and bucket and object_key:
            cache_key = (bucket, object_key)
            if cache_key in presign_cache:
                signed = presign_cache[cache_key]
            else:
                signed = presign_get(bucket, object_key, expires=900)
                presign_cache[cache_key] = signed
        out.append(
            {
                "id": av.id,
                "name": av.name,
                "description": av.description,
                "is_premium": av.is_premium,
                "purchase_date": purchased_at,
                "url": signed,
                "mime_type": getattr(av, "mime_type", None),
            }
        )
    return out


def buy_avatar(db, *, current_user, avatar_id: str, payment_method: str):
    from datetime import datetime

    from sqlalchemy.exc import IntegrityError

    from fastapi import HTTPException, status

    avatar = store_repository.get_avatar(db, avatar_id=avatar_id)
    if not avatar:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Avatar with ID {avatar_id} not found",
        )

    existing = store_repository.get_user_avatar_ownership(
        db, user_id=current_user.account_id, avatar_id=avatar_id
    )
    if existing:
        return {
            "status": "error",
            "message": f"You already own the avatar '{avatar.name}'",
            "item_id": avatar_id,
            "purchase_date": existing.purchase_date,
        }

    if payment_method == "gems":
        if avatar.price_gems is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Avatar '{avatar.name}' cannot be purchased with gems",
            )

        user = store_repository.lock_user(db, user_id=current_user.account_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        if user.gems < avatar.price_gems:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Not enough gems. You have {user.gems} gems, "
                    f"but this avatar costs {avatar.price_gems} gems"
                ),
            )

        user.gems -= avatar.price_gems

        try:
            ownership = store_repository.create_user_avatar_ownership(
                db,
                user_id=user.account_id,
                avatar_id=avatar_id,
                purchase_date=datetime.utcnow(),
            )
            db.commit()
            db.refresh(ownership)
            return {
                "status": "success",
                "message": f"Successfully purchased avatar '{avatar.name}' for {avatar.price_gems} gems",
                "item_id": avatar_id,
                "purchase_date": ownership.purchase_date,
                "gems_spent": avatar.price_gems,
            }
        except IntegrityError:
            db.rollback()
            existing = store_repository.get_user_avatar_ownership(
                db, user_id=user.account_id, avatar_id=avatar_id
            )
            return {
                "status": "success",
                "message": f"You already own the avatar '{avatar.name}'",
                "item_id": avatar_id,
                "purchase_date": existing.purchase_date,
                "gems_spent": 0,
            }

    if payment_method == "usd":
        if avatar.price_minor is None or avatar.price_minor == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Avatar '{avatar.name}' cannot be purchased with USD",
            )

        try:
            ownership = store_repository.create_user_avatar_ownership(
                db,
                user_id=current_user.account_id,
                avatar_id=avatar_id,
                purchase_date=datetime.utcnow(),
            )
            db.commit()
            db.refresh(ownership)
            return {
                "status": "success",
                "message": f"Successfully purchased avatar '{avatar.name}' for ${getattr(avatar, 'price_usd', None)}",
                "item_id": avatar_id,
                "purchase_date": ownership.purchase_date,
                "usd_spent": getattr(avatar, "price_usd", None),
            }
        except IntegrityError:
            db.rollback()
            existing = store_repository.get_user_avatar_ownership(
                db, user_id=current_user.account_id, avatar_id=avatar_id
            )
            return {
                "status": "success",
                "message": f"You already own the avatar '{avatar.name}'",
                "item_id": avatar_id,
                "purchase_date": existing.purchase_date,
                "usd_spent": 0,
            }

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Invalid payment method: {payment_method}. Must be 'gems' or 'usd'",
    )


def select_avatar(db, *, current_user, avatar_id: str):
    from fastapi import HTTPException, status

    avatar = store_repository.get_avatar(db, avatar_id=avatar_id)
    if not avatar:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Avatar with ID {avatar_id} not found",
        )

    ownership = store_repository.get_user_avatar_ownership(
        db, user_id=current_user.account_id, avatar_id=avatar_id
    )
    if not ownership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You don't own the avatar with ID {avatar_id}",
        )

    current_user.profile_pic_url = None
    current_user.selected_avatar_id = avatar_id
    db.commit()

    return {
        "status": "success",
        "message": f"Successfully selected avatar '{avatar.name}' as your profile avatar",
        "selected_id": avatar_id,
    }


def list_frames(db, *, current_user, skip: int, limit: int, include_urls: bool):
    key = f"store:frames:v1:skip={skip}:limit={limit}:urls={int(bool(include_urls))}"

    def _build():
        frames = store_repository.list_frames(db, skip=skip, limit=limit)
        out = []
        presign_cache = {}
        for fr in frames:
            signed = None
            bucket = getattr(fr, "bucket", None)
            object_key = getattr(fr, "object_key", None)
            if include_urls and bucket and object_key:
                cache_key = (bucket, object_key)
                if cache_key in presign_cache:
                    signed = presign_cache[cache_key]
                else:
                    signed = presign_get(bucket, object_key, expires=900)
                    presign_cache[cache_key] = signed

            out.append(
                {
                    "id": fr.id,
                    "name": fr.name,
                    "description": fr.description,
                    "price_gems": fr.price_gems,
                    "price_minor": fr.price_minor,
                    "price_usd": getattr(fr, "price_usd", None),
                    "is_premium": fr.is_premium,
                    "bucket": getattr(fr, "bucket", None),
                    "object_key": getattr(fr, "object_key", None),
                    "mime_type": getattr(fr, "mime_type", None),
                    "created_at": fr.created_at,
                    "url": signed,
                }
            )
        return out

    return default_cache.get_or_set(key, ttl_seconds=60, factory=_build)
    frames = store_repository.list_frames(db, skip=skip, limit=limit)
    out = []
    presign_cache = {}
    for fr in frames:
        signed = None
        bucket = getattr(fr, "bucket", None)
        object_key = getattr(fr, "object_key", None)
        if include_urls and bucket and object_key:
            cache_key = (bucket, object_key)
            if cache_key in presign_cache:
                signed = presign_cache[cache_key]
            else:
                signed = presign_get(bucket, object_key, expires=900)
                presign_cache[cache_key] = signed

        out.append(
            {
                "id": fr.id,
                "name": fr.name,
                "description": fr.description,
                "price_gems": fr.price_gems,
                "price_minor": fr.price_minor,
                "price_usd": getattr(fr, "price_usd", None),
                "is_premium": fr.is_premium,
                "bucket": getattr(fr, "bucket", None),
                "object_key": getattr(fr, "object_key", None),
                "mime_type": getattr(fr, "mime_type", None),
                "created_at": fr.created_at,
                "url": signed,
            }
        )
    return out


def list_owned_frames(db, *, current_user, include_urls: bool):
    rows = store_repository.list_user_owned_frames(db, user_id=current_user.account_id)
    out = []
    presign_cache = {}
    for fr, purchased_at in rows:
        signed = None
        bucket = getattr(fr, "bucket", None)
        object_key = getattr(fr, "object_key", None)
        if include_urls and bucket and object_key:
            cache_key = (bucket, object_key)
            if cache_key in presign_cache:
                signed = presign_cache[cache_key]
            else:
                signed = presign_get(bucket, object_key, expires=900)
                presign_cache[cache_key] = signed
        out.append(
            {
                "id": fr.id,
                "name": fr.name,
                "description": fr.description,
                "is_premium": fr.is_premium,
                "purchase_date": purchased_at,
                "url": signed,
                "mime_type": getattr(fr, "mime_type", None),
            }
        )
    return out


def buy_frame(db, *, current_user, frame_id: str, payment_method: str):
    from datetime import datetime

    from sqlalchemy.exc import IntegrityError

    from fastapi import HTTPException, status

    frame = store_repository.get_frame(db, frame_id=frame_id)
    if not frame:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Frame with ID {frame_id} not found",
        )

    existing = store_repository.get_user_frame_ownership(
        db, user_id=current_user.account_id, frame_id=frame_id
    )
    if existing:
        return {
            "status": "error",
            "message": f"You already own the frame '{frame.name}'",
            "item_id": frame_id,
            "purchase_date": existing.purchase_date,
        }

    if payment_method == "gems":
        if frame.price_gems is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Frame '{frame.name}' cannot be purchased with gems",
            )

        user = store_repository.lock_user(db, user_id=current_user.account_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        if user.gems < frame.price_gems:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Not enough gems. You have {user.gems} gems, "
                    f"but this frame costs {frame.price_gems} gems"
                ),
            )

        user.gems -= frame.price_gems

        try:
            ownership = store_repository.create_user_frame_ownership(
                db,
                user_id=user.account_id,
                frame_id=frame_id,
                purchase_date=datetime.utcnow(),
            )
            db.commit()
            db.refresh(ownership)
            return {
                "status": "success",
                "message": f"Successfully purchased frame '{frame.name}' for {frame.price_gems} gems",
                "item_id": frame_id,
                "purchase_date": ownership.purchase_date,
                "gems_spent": frame.price_gems,
            }
        except IntegrityError:
            db.rollback()
            existing = store_repository.get_user_frame_ownership(
                db, user_id=user.account_id, frame_id=frame_id
            )
            return {
                "status": "success",
                "message": f"You already own the frame '{frame.name}'",
                "item_id": frame_id,
                "purchase_date": existing.purchase_date,
                "gems_spent": 0,
            }

    if payment_method == "usd":
        if frame.price_minor is None or frame.price_minor == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Frame '{frame.name}' cannot be purchased with USD",
            )

        try:
            ownership = store_repository.create_user_frame_ownership(
                db,
                user_id=current_user.account_id,
                frame_id=frame_id,
                purchase_date=datetime.utcnow(),
            )
            db.commit()
            db.refresh(ownership)
            return {
                "status": "success",
                "message": f"Successfully purchased frame '{frame.name}' for ${getattr(frame, 'price_usd', None)}",
                "item_id": frame_id,
                "purchase_date": ownership.purchase_date,
                "usd_spent": getattr(frame, "price_usd", None),
            }
        except IntegrityError:
            db.rollback()
            existing = store_repository.get_user_frame_ownership(
                db, user_id=current_user.account_id, frame_id=frame_id
            )
            return {
                "status": "success",
                "message": f"You already own the frame '{frame.name}'",
                "item_id": frame_id,
                "purchase_date": existing.purchase_date,
                "usd_spent": 0,
            }

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Invalid payment method: {payment_method}. Must be 'gems' or 'usd'",
    )


def select_frame(db, *, current_user, frame_id: str):
    from fastapi import HTTPException, status

    frame = store_repository.get_frame(db, frame_id=frame_id)
    if not frame:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Frame with ID {frame_id} not found",
        )

    ownership = store_repository.get_user_frame_ownership(
        db, user_id=current_user.account_id, frame_id=frame_id
    )
    if not ownership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You don't own the frame with ID {frame_id}",
        )

    current_user.selected_frame_id = frame_id
    db.commit()
    return {
        "status": "success",
        "message": f"Successfully selected frame '{frame.name}' as your profile frame",
        "selected_id": frame_id,
    }
