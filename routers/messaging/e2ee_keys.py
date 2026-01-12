from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.db import get_db
from routers.dependencies import get_current_user

from .schemas import (
    E2EEClaimPrekeyRequest,
    E2EERevokeDeviceRequest,
    E2EEUploadKeyBundleRequest,
)
from .service import (
    claim_e2ee_prekey as service_claim_e2ee_prekey,
    get_e2ee_key_bundle as service_get_e2ee_key_bundle,
    list_e2ee_devices as service_list_e2ee_devices,
    revoke_e2ee_device as service_revoke_e2ee_device,
    upload_e2ee_key_bundle as service_upload_e2ee_key_bundle,
)

router = APIRouter(prefix="/e2ee", tags=["E2EE Keys"])


@router.post("/keys/upload")
def upload_key_bundle(
    request: E2EEUploadKeyBundleRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Upload or update device key bundle and one-time prekeys.
    Creates device if it doesn't exist.
    """
    return service_upload_e2ee_key_bundle(db, current_user=current_user, request=request)


@router.get("/keys/bundle")
def get_key_bundle(
    user_id: int,
    bundle_version: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Get key bundles for all active devices of a user.
    Excludes revoked devices.
    If bundle_version provided, checks for staleness.
    """
    return service_get_e2ee_key_bundle(
        db, current_user=current_user, user_id=user_id, bundle_version=bundle_version
    )


@router.get("/devices")
def list_devices(
    db: Session = Depends(get_db), current_user = Depends(get_current_user)
):
    """
    List all devices for the current user.
    """
    return service_list_e2ee_devices(db, current_user=current_user)


@router.post("/devices/revoke")
def revoke_device(
    request: E2EERevokeDeviceRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Revoke a device. Marks it as revoked and logs the revocation.
    """
    return service_revoke_e2ee_device(db, current_user=current_user, request=request)


@router.post("/prekeys/claim")
def claim_prekey(
    request: E2EEClaimPrekeyRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Claim a one-time prekey during X3DH session setup.
    Marks the prekey as claimed atomically.
    """
    return service_claim_e2ee_prekey(db, current_user=current_user, request=request)
