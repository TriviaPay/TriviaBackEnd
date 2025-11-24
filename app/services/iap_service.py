"""
IAP Service - Handles In-App Purchase verification for Apple and Google

NOTE: This module is maintained for backward compatibility.
New implementations should use:
- app.services.apple_iap_service.process_apple_iap
- app.services.google_iap_service.process_google_iap
- app.services.product_pricing.get_price_minor_for_product_id
"""
import logging
from typing import Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.product_pricing import get_price_minor_for_product_id
from app.services.apple_iap_service import process_apple_iap
from app.services.google_iap_service import process_google_iap

logger = logging.getLogger(__name__)


async def verify_apple_receipt(
    receipt_data: str,
    product_id: str,
    environment: str = "production"
) -> Dict:
    """
    Verify Apple receipt and return transaction details.
    
    DEPRECATED: Use app.services.apple_iap_service.process_apple_iap instead.
    This function is maintained for backward compatibility.
    
    Args:
        receipt_data: Base64-encoded receipt data
        product_id: Product ID from the receipt
        environment: 'production' or 'sandbox'
        
    Returns:
        Dict with verification status and transaction details:
        {
            'verified': bool,
            'transaction_id': str,
            'product_id': str,
            'purchase_date': str (ISO format),
            'environment': str
        }
    """
    logger.warning(
        "verify_apple_receipt is deprecated. Use app.services.apple_iap_service.process_apple_iap instead."
    )
    
    # Return mock response for backward compatibility
    # Real implementation should use process_apple_iap
    return {
        "verified": False,
        "transaction_id": "",
        "product_id": product_id,
        "purchase_date": None,
        "environment": environment,
        "error": "This function is deprecated. Use process_apple_iap instead."
    }


async def verify_google_purchase(
    package_name: str,
    product_id: str,
    purchase_token: str
) -> Dict:
    """
    Verify Google Play purchase and return transaction details.
    
    DEPRECATED: Use app.services.google_iap_service.process_google_iap instead.
    This function is maintained for backward compatibility.
    
    Args:
        package_name: Android app package name
        product_id: Product ID from the purchase
        purchase_token: Purchase token from Google Play
        
    Returns:
        Dict with verification status and transaction details:
        {
            'verified': bool,
            'transaction_id': str,
            'product_id': str,
            'purchase_time': int (milliseconds),
            'purchase_state': int
        }
    """
    logger.warning(
        "verify_google_purchase is deprecated. Use app.services.google_iap_service.process_google_iap instead."
    )
    
    # Return mock response for backward compatibility
    # Real implementation should use process_google_iap
    return {
        "verified": False,
        "transaction_id": "",
        "product_id": product_id,
        "purchase_time": None,
        "purchase_state": None,
        "error": "This function is deprecated. Use process_google_iap instead."
    }


async def get_product_credit_amount(
    db: AsyncSession,
    product_id: str,
    platform: Optional[str] = None
) -> Optional[int]:
    """
    Get credited amount in minor units for a product ID from product tables.
    
    DEPRECATED: Use app.services.product_pricing.get_price_minor_for_product_id instead.
    This function is maintained for backward compatibility.
    
    Args:
        db: Async database session
        product_id: Product ID (e.g., "AV001", "GP001", "FR001", "BD001")
        platform: Optional platform filter (unused, kept for API compatibility)
        
    Returns:
        Amount in minor units (cents) or None if not found
        
    Note:
        This function now delegates to get_price_minor_for_product_id.
        It returns None instead of raising HTTPException for backward compatibility.
    """
    try:
        return await get_price_minor_for_product_id(db, product_id)
    except Exception:
        # Return None for backward compatibility (old code expected None, not exception)
        return None

