import os
from typing import Optional
import logging

try:
    import boto3
    from botocore.config import Config
except Exception:  # keep import-time safe where boto3 may not be installed yet
    boto3 = None
    Config = None

# AWS S3 configuration
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-2")  # Default to us-east-2 (set AWS_REGION env var to override)

_s3_client = None
_cached_region = None
_cached_key_id = None

def _get_s3():
    """Get or create S3 client for AWS"""
    global _s3_client, _cached_region, _cached_key_id
    # Invalidate cached client if region or credentials changed
    if _s3_client is not None:
        if _cached_region != AWS_REGION or _cached_key_id != AWS_ACCESS_KEY_ID:
            _s3_client = None
        else:
            return _s3_client
    if boto3 is None:
        raise RuntimeError("boto3 is required for presigning S3 URLs")
    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        raise RuntimeError("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set")
    
    session = boto3.session.Session()
    # Force signature version 4 (AWS4-HMAC-SHA256) - required by modern S3
    # Use virtual-hosted-style addressing and ensure regional endpoint
    config = Config(
        signature_version='s3v4',
        s3={
            'addressing_style': 'virtual',  # Use bucket.s3.region.amazonaws.com format
        }
    ) if Config else None
    _s3_client = session.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        config=config,
    )
    _cached_region = AWS_REGION
    _cached_key_id = AWS_ACCESS_KEY_ID
    return _s3_client

def presign_get(bucket: str, key: str, expires: int = 900) -> Optional[str]:
    """
    Generate a presigned URL for an S3 object.
    
    Args:
        bucket: S3 bucket name
        key: S3 object key (path)
        expires: URL expiration time in seconds (default 15 minutes)
    
    Returns:
        Presigned URL string or None if generation fails
    """
    if not bucket or not key:
        return None
    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        logging.warning("AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY not set - cannot generate presigned URLs")
        return None
    try:
        s3 = _get_s3()
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires,
        )
    except Exception as e:
        logging.error(f"Error generating presigned URL for bucket={bucket}, key={key}: {e}", exc_info=True)
        return None
