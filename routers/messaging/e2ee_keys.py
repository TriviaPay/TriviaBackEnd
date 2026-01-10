import hashlib
import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, update
from sqlalchemy.orm import Session

import models as models_module
from config import (
    E2EE_DM_ENABLED,
    E2EE_DM_IDENTITY_CHANGE_ALERT_THRESHOLD,
    E2EE_DM_IDENTITY_CHANGE_BLOCK_THRESHOLD,
    E2EE_DM_OTPK_CRITICAL_WATERMARK,
    E2EE_DM_OTPK_LOW_WATERMARK,
    E2EE_DM_PREKEY_POOL_SIZE,
    E2EE_DM_SIGNED_PREKEY_MAX_AGE_DAYS,
    E2EE_DM_SIGNED_PREKEY_ROTATION_DAYS,
)
from db import get_db
from models import DeviceRevocation, E2EEDevice, E2EEKeyBundle, E2EEOneTimePrekey, User
from routers.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/e2ee", tags=["E2EE Keys"])


class OneTimePrekeyRequest(BaseModel):
    prekey_pub: str = Field(
        ...,
        description="Base64 encoded one-time prekey public key",
        example="dGVzdF9wcmVrZXlfcHVibGljX2tleV8xMjM0NTY3ODkwYWJjZGVm",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "prekey_pub": "dGVzdF9wcmVrZXlfcHVibGljX2tleV8xMjM0NTY3ODkwYWJjZGVm"
            }
        }


class UploadKeyBundleRequest(BaseModel):
    device_id: Optional[str] = Field(
        None,
        description="Device UUID (optional, will be generated if not provided)",
        example="550e8400-e29b-41d4-a716-446655440000",
    )
    device_name: str = Field(
        ..., description="Device name/identifier", example="iPhone 15 Pro"
    )
    identity_key_pub: str = Field(
        ...,
        description="Base64 encoded identity public key",
        example="dGVzdF9pZGVudGl0eV9wdWJsaWNfa2V5XzEyMzQ1Njc4OTBhYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5eg==",
    )
    signed_prekey_pub: str = Field(
        ...,
        description="Base64 encoded signed prekey public key",
        example="dGVzdF9zaWduZWRfcHJla2V5X3B1YmxpY19rZXlfMTIzNDU2Nzg5MGFiY2RlZmdoaWprbG1ub3BxcnN0dXZ3eHl6",
    )
    signed_prekey_sig: str = Field(
        ...,
        description="Base64 encoded signature of signed prekey",
        example="dGVzdF9zaWduYXR1cmVfb2Zfc2lnbmVkX3ByZWtleV8xMjM0NTY3ODkwYWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=",
    )
    one_time_prekeys: List[OneTimePrekeyRequest] = Field(
        ..., description="List of one-time prekeys", min_items=1
    )

    class Config:
        json_schema_extra = {
            "example": {
                "device_id": "550e8400-e29b-41d4-a716-446655440000",
                "device_name": "iPhone 15 Pro",
                "identity_key_pub": "dGVzdF9pZGVudGl0eV9wdWJsaWNfa2V5XzEyMzQ1Njc4OTBhYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5eg==",
                "signed_prekey_pub": "dGVzdF9zaWduZWRfcHJla2V5X3B1YmxpY19rZXlfMTIzNDU2Nzg5MGFiY2RlZmdoaWprbG1ub3BxcnN0dXZ3eHl6",
                "signed_prekey_sig": "dGVzdF9zaWduYXR1cmVfb2Zfc2lnbmVkX3ByZWtleV8xMjM0NTY3ODkwYWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=",
                "one_time_prekeys": [
                    {
                        "prekey_pub": "dGVzdF9wcmVrZXlfcHVibGljX2tleV8xMjM0NTY3ODkwYWJjZGVm"
                    }
                ],
            }
        }


class RevokeDeviceRequest(BaseModel):
    device_id: str = Field(
        ...,
        description="Device UUID to revoke",
        example="550e8400-e29b-41d4-a716-446655440000",
    )
    reason: Optional[str] = Field(
        None, description="Reason for revocation", example="Device lost or stolen"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "device_id": "550e8400-e29b-41d4-a716-446655440000",
                "reason": "Device lost or stolen",
            }
        }


class ClaimPrekeyRequest(BaseModel):
    device_id: str = Field(
        ..., description="Device UUID", example="550e8400-e29b-41d4-a716-446655440000"
    )
    prekey_id: int = Field(..., description="One-time prekey ID to claim", example=1)

    class Config:
        json_schema_extra = {
            "example": {
                "device_id": "550e8400-e29b-41d4-a716-446655440000",
                "prekey_id": 1,
            }
        }


def _has_dm_relationship(db: Session, user_a: int, user_b: int) -> bool:
    """Check if two users share a DM conversation when models are available."""
    dm_participant = getattr(models_module, "DMParticipant", None)
    dm_conversation = getattr(models_module, "DMConversation", None)
    if not dm_participant:
        logger.warning("DMParticipant model not available; skipping relationship check")
        return True

    query = (
        db.query(dm_participant.conversation_id)
        .filter(dm_participant.user_id.in_([user_a, user_b]))
        .group_by(dm_participant.conversation_id)
        .having(func.count() == 2)
    )

    if dm_conversation:
        query = query.join(
            dm_conversation, dm_conversation.id == dm_participant.conversation_id
        )

    return query.first() is not None


@router.post("/keys/upload")
def upload_key_bundle(
    request: UploadKeyBundleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload or update device key bundle and one-time prekeys.
    Creates device if it doesn't exist.
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")

    # Validate request
    if not request.one_time_prekeys or len(request.one_time_prekeys) == 0:
        raise HTTPException(
            status_code=400, detail="At least one one-time prekey is required"
        )
    if len(request.one_time_prekeys) > E2EE_DM_PREKEY_POOL_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Too many one-time prekeys (max {E2EE_DM_PREKEY_POOL_SIZE})",
        )

    try:
        # Parse device_id or generate new one
        if request.device_id:
            try:
                device_uuid = uuid.UUID(request.device_id)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid device_id format: {request.device_id}",
                )
        else:
            device_uuid = uuid.uuid4()

        # Find device by device_id first (device_id is globally unique)
        device = (
            db.query(E2EEDevice).filter(E2EEDevice.device_id == device_uuid).first()
        )

        if not device:
            # Device doesn't exist, create new one
            device = E2EEDevice(
                device_id=device_uuid,
                user_id=current_user.account_id,
                device_name=request.device_name,
                status="active",
            )
            db.add(device)
            db.flush()
        else:
            # Device exists - check ownership
            if device.user_id != current_user.account_id:
                raise HTTPException(
                    status_code=403,
                    detail="Device belongs to another user. Cannot upload keys for this device.",
                )

            # Update device name and last seen
            device.device_name = request.device_name
            device.last_seen_at = datetime.utcnow()
            if device.status == "revoked":
                raise HTTPException(status_code=403, detail="Device has been revoked")

        # Find or create key bundle
        key_bundle = (
            db.query(E2EEKeyBundle)
            .filter(E2EEKeyBundle.device_id == device_uuid)
            .first()
        )

        # Track identity key changes for security monitoring
        identity_changed = False
        old_identity_key = None

        if key_bundle:
            old_identity_key = key_bundle.identity_key_pub
            if key_bundle.identity_key_pub != request.identity_key_pub:
                identity_changed = True
                # Log identity key change as security event
                logger.warning(
                    f"Identity key change detected: device={device_uuid}, "
                    f"user={current_user.account_id}, "
                    f"old_fingerprint={hashlib.sha256(old_identity_key.encode()).hexdigest()[:16]}, "
                    f"new_fingerprint={hashlib.sha256(request.identity_key_pub.encode()).hexdigest()[:16]}"
                )

            if identity_changed:
                identity_change_count = (
                    db.query(DeviceRevocation)
                    .filter(
                        DeviceRevocation.device_id == device_uuid,
                        DeviceRevocation.reason.in_(
                            ["identity_change", "identity_change_block"]
                        ),
                    )
                    .count()
                    + 1
                )

                if (
                    E2EE_DM_IDENTITY_CHANGE_BLOCK_THRESHOLD > 0
                    and identity_change_count >= E2EE_DM_IDENTITY_CHANGE_BLOCK_THRESHOLD
                ):
                    device.status = "revoked"
                    db.add(
                        DeviceRevocation(
                            user_id=current_user.account_id,
                            device_id=device_uuid,
                            reason="identity_change_block",
                        )
                    )
                    db.commit()
                    raise HTTPException(
                        status_code=409,
                        detail="IDENTITY_CHANGE_BLOCKED",
                        headers={"X-Error-Code": "IDENTITY_CHANGE_BLOCKED"},
                    )

                if (
                    E2EE_DM_IDENTITY_CHANGE_ALERT_THRESHOLD > 0
                    and identity_change_count >= E2EE_DM_IDENTITY_CHANGE_ALERT_THRESHOLD
                ):
                    logger.warning(
                        "Identity key change alert threshold reached",
                        extra={
                            "user_id": current_user.account_id,
                            "device_id": str(device_uuid),
                            "count": identity_change_count,
                        },
                    )
                db.add(
                    DeviceRevocation(
                        user_id=current_user.account_id,
                        device_id=device_uuid,
                        reason="identity_change",
                    )
                )

            # Update existing bundle
            key_bundle.identity_key_pub = request.identity_key_pub
            key_bundle.signed_prekey_pub = request.signed_prekey_pub
            key_bundle.signed_prekey_sig = request.signed_prekey_sig
            key_bundle.bundle_version += 1
            key_bundle.updated_at = datetime.utcnow()
        else:
            # Create new bundle
            key_bundle = E2EEKeyBundle(
                device_id=device_uuid,
                identity_key_pub=request.identity_key_pub,
                signed_prekey_pub=request.signed_prekey_pub,
                signed_prekey_sig=request.signed_prekey_sig,
                bundle_version=1,
                prekeys_remaining=0,
            )
            db.add(key_bundle)

        # Delete old unclaimed prekeys (optional cleanup)
        # Keep claimed ones for audit, but we'll focus on unclaimed
        db.query(E2EEOneTimePrekey).filter(
            E2EEOneTimePrekey.device_id == device_uuid,
            E2EEOneTimePrekey.claimed == False,
        ).delete(synchronize_session=False)

        # Add new one-time prekeys in bulk
        prekey_objects = [
            E2EEOneTimePrekey(
                device_id=device_uuid, prekey_pub=prekey_req.prekey_pub, claimed=False
            )
            for prekey_req in request.one_time_prekeys
        ]
        db.bulk_save_objects(prekey_objects)
        prekeys_stored = len(prekey_objects)

        # Update prekeys_remaining count
        key_bundle.prekeys_remaining = prekeys_stored

        db.commit()
        db.refresh(key_bundle)

        logger.info(
            f"Key bundle uploaded for device {device_uuid} (user {current_user.account_id})"
        )

        return {
            "device_id": str(device_uuid),
            "bundle_version": key_bundle.bundle_version,
            "prekeys_stored": prekeys_stored,
        }

    except ValueError as e:
        db.rollback()
        logger.error(f"Invalid UUID in upload_key_bundle: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Invalid UUID: {str(e)}")
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error uploading key bundle: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to upload key bundle: {str(e)}"
        )


@router.get("/keys/bundle")
def get_key_bundle(
    user_id: int,
    bundle_version: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get key bundles for all active devices of a user.
    Excludes revoked devices.
    If bundle_version provided, checks for staleness.
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")

    # Check if user is blocked (either direction)
    from models import Block

    is_blocked = (
        db.query(Block)
        .filter(
            or_(
                and_(
                    Block.blocker_id == user_id,
                    Block.blocked_id == current_user.account_id,
                ),
                and_(
                    Block.blocker_id == current_user.account_id,
                    Block.blocked_id == user_id,
                ),
            )
        )
        .first()
    )
    if is_blocked:
        raise HTTPException(
            status_code=403, detail="BLOCKED", headers={"X-Error-Code": "BLOCKED"}
        )

    if user_id != current_user.account_id and not _has_dm_relationship(
        db, user_id, current_user.account_id
    ):
        raise HTTPException(status_code=403, detail="RELATIONSHIP_REQUIRED")

    # Get all active devices for the user (exclude revoked) with bundles and prekey counts
    rows = (
        db.query(
            E2EEDevice.device_id,
            E2EEDevice.device_name,
            E2EEKeyBundle.identity_key_pub,
            E2EEKeyBundle.signed_prekey_pub,
            E2EEKeyBundle.signed_prekey_sig,
            E2EEKeyBundle.bundle_version,
            func.count(E2EEOneTimePrekey.id)
            .filter(E2EEOneTimePrekey.claimed == False)
            .label("available"),
        )
        .join(E2EEKeyBundle, E2EEKeyBundle.device_id == E2EEDevice.device_id)
        .outerjoin(
            E2EEOneTimePrekey, E2EEOneTimePrekey.device_id == E2EEDevice.device_id
        )
        .filter(E2EEDevice.user_id == user_id, E2EEDevice.status == "active")
        .group_by(
            E2EEDevice.device_id,
            E2EEDevice.device_name,
            E2EEKeyBundle.identity_key_pub,
            E2EEKeyBundle.signed_prekey_pub,
            E2EEKeyBundle.signed_prekey_sig,
            E2EEKeyBundle.bundle_version,
        )
        .all()
    )

    result_devices = []
    for row in rows:
        if bundle_version is not None and row.bundle_version > bundle_version:
            raise HTTPException(
                status_code=409,
                detail="BUNDLE_STALE",
                headers={
                    "X-Error-Code": "BUNDLE_STALE",
                    "X-Bundle-Version": str(row.bundle_version),
                },
            )

        result_devices.append(
            {
                "device_id": str(row.device_id),
                "device_name": row.device_name,
                "identity_key_pub": row.identity_key_pub,
                "signed_prekey_pub": row.signed_prekey_pub,
                "signed_prekey_sig": row.signed_prekey_sig,
                "bundle_version": row.bundle_version,
                "prekeys_available": int(row.available or 0),
            }
        )

    return {"devices": result_devices}


@router.get("/devices")
def list_devices(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """
    List all devices for the current user.
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")

    devices = (
        db.query(E2EEDevice)
        .filter(E2EEDevice.user_id == current_user.account_id)
        .order_by(E2EEDevice.created_at.desc())
        .all()
    )

    return {
        "devices": [
            {
                "device_id": str(device.device_id),
                "device_name": device.device_name,
                "created_at": device.created_at.isoformat(),
                "last_seen_at": (
                    device.last_seen_at.isoformat() if device.last_seen_at else None
                ),
                "status": device.status,
            }
            for device in devices
        ]
    }


@router.post("/devices/revoke")
def revoke_device(
    request: RevokeDeviceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Revoke a device. Marks it as revoked and logs the revocation.
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")

    try:
        device_uuid = uuid.UUID(request.device_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid device UUID")

    device = (
        db.query(E2EEDevice)
        .filter(
            E2EEDevice.device_id == device_uuid,
            E2EEDevice.user_id == current_user.account_id,
        )
        .first()
    )

    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if device.status == "revoked":
        return {"success": True, "message": "Device already revoked"}

    # Mark device as revoked
    device.status = "revoked"

    # Log revocation
    revocation = DeviceRevocation(
        user_id=current_user.account_id, device_id=device_uuid, reason=request.reason
    )
    db.add(revocation)

    db.commit()

    logger.info(f"Device {device_uuid} revoked by user {current_user.account_id}")

    return {"success": True}


@router.post("/prekeys/claim")
def claim_prekey(
    request: ClaimPrekeyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Claim a one-time prekey during X3DH session setup.
    Marks the prekey as claimed atomically.
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")

    try:
        device_uuid = uuid.UUID(request.device_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid device UUID")

    # Check if device is revoked
    device = db.query(E2EEDevice).filter(E2EEDevice.device_id == device_uuid).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if device.status == "revoked":
        raise HTTPException(
            status_code=409,
            detail="DEVICE_REVOKED",
            headers={"X-Error-Code": "DEVICE_REVOKED"},
        )

    if device.user_id != current_user.account_id:
        from models import Block

        is_blocked = (
            db.query(Block)
            .filter(
                or_(
                    and_(
                        Block.blocker_id == device.user_id,
                        Block.blocked_id == current_user.account_id,
                    ),
                    and_(
                        Block.blocker_id == current_user.account_id,
                        Block.blocked_id == device.user_id,
                    ),
                )
            )
            .first()
        )
        if is_blocked:
            raise HTTPException(
                status_code=403, detail="BLOCKED", headers={"X-Error-Code": "BLOCKED"}
            )

        if not _has_dm_relationship(db, device.user_id, current_user.account_id):
            raise HTTPException(status_code=403, detail="RELATIONSHIP_REQUIRED")

    # Atomically claim the prekey
    result = db.execute(
        update(E2EEOneTimePrekey)
        .where(
            E2EEOneTimePrekey.id == request.prekey_id,
            E2EEOneTimePrekey.device_id == device_uuid,
            E2EEOneTimePrekey.claimed == False,
        )
        .values(claimed=True)
        .returning(E2EEOneTimePrekey.id)
    )
    claimed_id = result.scalar_one_or_none()

    if not claimed_id:
        # Check if pool is exhausted
        available_count = (
            db.query(E2EEOneTimePrekey)
            .filter(
                E2EEOneTimePrekey.device_id == device_uuid,
                E2EEOneTimePrekey.claimed == False,
            )
            .count()
        )

        if available_count == 0:
            key_bundle = (
                db.query(E2EEKeyBundle)
                .filter(E2EEKeyBundle.device_id == device_uuid)
                .first()
            )
            bundle_version = key_bundle.bundle_version if key_bundle else 1

            raise HTTPException(
                status_code=409,
                detail="PREKEYS_EXHAUSTED",
                headers={
                    "X-Error-Code": "PREKEYS_EXHAUSTED",
                    "X-Bundle-Version": str(bundle_version),
                },
            )
        else:
            raise HTTPException(
                status_code=404, detail="Prekey not found or already claimed"
            )

    # Update bundle's prekeys_remaining count
    remaining = (
        db.query(func.count(E2EEOneTimePrekey.id))
        .filter(
            E2EEOneTimePrekey.device_id == device_uuid,
            E2EEOneTimePrekey.claimed == False,
        )
        .scalar()
        or 0
    )
    key_bundle = (
        db.query(E2EEKeyBundle).filter(E2EEKeyBundle.device_id == device_uuid).first()
    )

    if key_bundle:
        key_bundle.prekeys_remaining = remaining

    db.commit()

    return {"claimed": True, "prekey_id": request.prekey_id}
