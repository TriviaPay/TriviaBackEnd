"""
Apple IAP Service - StoreKit 2 signed transaction verification
"""

import base64
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import jwt
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import core.config as config
from app.models.user import User
from app.models.wallet import IapEvent, IapReceipt
from app.services.product_pricing import get_product_info
from app.services.wallet_service import adjust_wallet_balance

logger = logging.getLogger(__name__)

APPLE_PRODUCT_TYPE_MAP = {
    "consumable": "consumable",
    "non-consumable": "non_consumable",
    "non consumable": "non_consumable",
    "non_consumable": "non_consumable",
    "auto-renewable subscription": "subscription",
    "non-renewing subscription": "subscription",
    "subscription": "subscription",
}


def _load_root_certs() -> List[x509.Certificate]:
    paths = config.APPLE_ROOT_CERT_PATHS
    if not paths:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Apple root certs not configured (APPLE_ROOT_CERT_PATHS)",
        )

    certs: List[x509.Certificate] = []
    for path in paths:
        try:
            with open(path, "rb") as fh:
                certs.append(x509.load_pem_x509_certificate(fh.read()))
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to load Apple root cert: {path} ({exc})",
            )
    return certs


def _verify_cert_signature(cert: x509.Certificate, issuer: x509.Certificate) -> None:
    pub = issuer.public_key()
    if isinstance(pub, rsa.RSAPublicKey):
        pub.verify(
            cert.signature,
            cert.tbs_certificate_bytes,
            padding.PKCS1v15(),
            cert.signature_hash_algorithm,
        )
    elif isinstance(pub, ec.EllipticCurvePublicKey):
        pub.verify(
            cert.signature,
            cert.tbs_certificate_bytes,
            ec.ECDSA(cert.signature_hash_algorithm),
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unsupported certificate public key type",
        )


def _verify_cert_chain(chain: List[x509.Certificate], roots: List[x509.Certificate]) -> None:
    if not chain:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Empty certificate chain"
        )

    now = datetime.now(timezone.utc)
    for cert in chain:
        if cert.not_valid_before > now or cert.not_valid_after < now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Certificate in chain is not valid at current time",
            )

    # Verify each cert is signed by the next cert in the chain
    for idx in range(len(chain) - 1):
        _verify_cert_signature(chain[idx], chain[idx + 1])

    # Verify the last cert is signed by one of the trusted roots
    last = chain[-1]
    for root in roots:
        try:
            _verify_cert_signature(last, root)
            return
        except Exception:
            continue

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Certificate chain is not trusted by configured Apple root certs",
    )


def verify_signed_transaction_info(signed_transaction_info: str) -> Dict[str, Any]:
    try:
        header = jwt.get_unverified_header(signed_transaction_info)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signed_transaction_info header",
        )

    x5c = header.get("x5c")
    if not x5c:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="signed_transaction_info missing x5c certificate chain",
        )

    try:
        chain = [
            x509.load_der_x509_certificate(base64.b64decode(cert))
            for cert in x5c
        ]
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to parse x5c certificate chain",
        )

    roots = _load_root_certs()
    _verify_cert_chain(chain, roots)

    try:
        payload = jwt.decode(
            signed_transaction_info,
            key=chain[0].public_key(),
            algorithms=[header.get("alg", "RS256")],
            options={
                "verify_aud": False,
                "verify_iss": False,
                "verify_exp": False,
            },
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signed_transaction_info signature",
        )

    return payload


def _normalize_environment(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    lowered = value.lower()
    if "sandbox" in lowered:
        return "sandbox"
    if "prod" in lowered:
        return "production"
    return lowered


async def process_apple_iap(
    db: AsyncSession,
    user: User,
    signed_transaction_info: str,
    product_id: str,
    environment: str,
    app_account_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    High-level StoreKit 2 verification and wallet crediting.
    """
    if not signed_transaction_info:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="signed_transaction_info is required",
        )

    payload = verify_signed_transaction_info(signed_transaction_info)

    bundle_id = payload.get("bundleId")
    if config.APPLE_APP_BUNDLE_ID and bundle_id != config.APPLE_APP_BUNDLE_ID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bundle ID does not match configured app bundle",
        )

    payload_env = _normalize_environment(payload.get("environment"))
    expected_env = _normalize_environment(environment or "production")
    if payload_env and expected_env and payload_env != expected_env:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Environment mismatch for signed transaction",
        )

    confirmed_product_id = payload.get("productId")
    if confirmed_product_id != product_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Product ID mismatch: expected '{product_id}', got '{confirmed_product_id}'",
        )

    payload_app_account = payload.get("appAccountToken")
    if app_account_token:
        if not payload_app_account or payload_app_account != app_account_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="app_account_token does not match signed transaction",
            )

    transaction_id = payload.get("transactionId")
    original_transaction_id = payload.get("originalTransactionId")
    web_order_line_item_id = payload.get("webOrderLineItemId")
    purchase_time_ms = payload.get("purchaseDate")
    revocation_date_ms = payload.get("revocationDate")
    revocation_reason = payload.get("revocationReason")

    if not transaction_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing transactionId in signed transaction",
        )

    refund_stmt = (
        select(IapEvent)
        .where(
            and_(
                IapEvent.platform == "apple",
                IapEvent.transaction_id == transaction_id,
                IapEvent.notification_type.in_(("REFUND", "REVOKE")),
            )
        )
        .limit(1)
    )
    refund_result = await db.execute(refund_stmt)
    refund_event = refund_result.scalar_one_or_none()
    if refund_event:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Transaction has been revoked by Apple",
        )

    product_info = await get_product_info(db, product_id)
    price_minor = product_info["price_minor"]
    product_type = product_info["product_type"]

    payload_product_type = payload.get("productType") or payload.get("type")
    if payload_product_type:
        mapped_type = APPLE_PRODUCT_TYPE_MAP.get(str(payload_product_type).lower())
        if mapped_type and mapped_type != product_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Product type mismatch: expected "
                    f"'{product_type}', got '{payload_product_type}'"
                ),
            )

    try:
        stmt = (
            select(IapReceipt)
            .where(
                and_(
                    IapReceipt.platform == "apple",
                    IapReceipt.transaction_id == transaction_id,
                )
            )
            .with_for_update()
        )
        result = await db.execute(stmt)
        receipt = result.scalar_one_or_none()

        if receipt and receipt.status in ("credited", "consumed"):
            user_stmt = select(User).where(User.account_id == user.account_id)
            user_result = await db.execute(user_stmt)
            current_user = user_result.scalar_one_or_none()
            current_balance = current_user.wallet_balance_minor if current_user else 0
            return {
                "success": True,
                "platform": "apple",
                "transaction_id": transaction_id,
                "product_id": confirmed_product_id,
                "credited_amount_minor": receipt.credited_amount_minor,
                "new_balance_minor": current_balance,
                "receipt_id": receipt.id,
                "already_processed": True,
            }
        if receipt and receipt.status == "revoked":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Transaction has been revoked by Apple",
            )

        if not receipt:
            receipt = IapReceipt(
                user_id=user.account_id,
                platform="apple",
                transaction_id=transaction_id,
                original_transaction_id=original_transaction_id,
                web_order_line_item_id=web_order_line_item_id,
                product_id=confirmed_product_id,
                bundle_id=bundle_id,
                environment=payload_env,
                product_type=product_type,
                receipt_data=signed_transaction_info,
                status="received",
                credited_amount_minor=None,
                purchase_time_ms=int(purchase_time_ms) if purchase_time_ms else None,
                revocation_date=(
                    datetime.fromtimestamp(int(revocation_date_ms) / 1000, tz=timezone.utc)
                    if revocation_date_ms
                    else None
                ),
                revocation_reason=str(revocation_reason) if revocation_reason else None,
                app_account_token=payload_app_account,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(receipt)
            await db.flush()

        if revocation_date_ms:
            receipt.status = "revoked"
            receipt.updated_at = datetime.utcnow()
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Transaction has been revoked by Apple",
            )

        receipt.status = "verified"
        receipt.credited_amount_minor = price_minor
        receipt.updated_at = datetime.utcnow()

        new_balance = await adjust_wallet_balance(
            db=db,
            user_id=user.account_id,
            currency="usd",
            delta_minor=price_minor,
            kind="deposit",
            external_ref_type="iap_receipt",
            external_ref_id=str(receipt.id),
            event_id=f"apple:{transaction_id}",
            livemode=(payload_env == "production"),
        )

        receipt.status = "credited"
        receipt.updated_at = datetime.utcnow()
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing_stmt = select(IapReceipt).where(
            and_(
                IapReceipt.platform == "apple",
                IapReceipt.transaction_id == transaction_id,
            )
        )
        existing_result = await db.execute(existing_stmt)
        existing = existing_result.scalar_one_or_none()
        if existing:
            user_stmt = select(User).where(User.account_id == user.account_id)
            user_result = await db.execute(user_stmt)
            current_user = user_result.scalar_one_or_none()
            current_balance = current_user.wallet_balance_minor if current_user else 0
            return {
                "success": True,
                "platform": "apple",
                "transaction_id": transaction_id,
                "product_id": confirmed_product_id,
                "credited_amount_minor": existing.credited_amount_minor,
                "new_balance_minor": current_balance,
                "receipt_id": existing.id,
                "already_processed": True,
            }
        raise
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await db.rollback()
        logger.error("Apple IAP processing failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process Apple IAP",
        )

    logger.info(
        "Apple IAP processed: user=%s, product=%s, transaction=%s, amount=%s, balance=%s",
        user.account_id,
        product_id,
        transaction_id,
        price_minor,
        new_balance,
    )

    return {
        "success": True,
        "platform": "apple",
        "transaction_id": transaction_id,
        "product_id": confirmed_product_id,
        "credited_amount_minor": price_minor,
        "new_balance_minor": new_balance,
        "receipt_id": receipt.id,
        "already_processed": False,
    }
