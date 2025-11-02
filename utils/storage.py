import os
from typing import Optional, Dict
import logging

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
except Exception:  # keep import-time safe where boto3 may not be installed yet
    boto3 = None
    Config = None
    ClientError = None

# AWS S3 configuration
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-2")  # Default to us-east-2 (set AWS_REGION env var to override)

_s3_clients: Dict[str, any] = {}  # Cache clients by region
_bucket_regions: Dict[str, str] = {}  # Cache bucket regions
_cached_key_id = None

def _get_bucket_region(bucket: str) -> str:
    """
    Get the region for a specific bucket.
    Auto-detects the bucket region to avoid PermanentRedirect errors.
    """
    global _bucket_regions
    
    # Return cached region if available
    if bucket in _bucket_regions:
        return _bucket_regions[bucket]
    
    if boto3 is None:
        raise RuntimeError("boto3 is required for presigning S3 URLs")
    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        raise RuntimeError("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set")
    
    # First, try to get bucket location using a default region client
    try:
        session = boto3.session.Session()
        # Use us-east-1 for get_bucket_location (it's the only region that supports this without explicit region)
        temp_client = session.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        )
        
        response = temp_client.get_bucket_location(Bucket=bucket)
        region = response.get("LocationConstraint")
        
        # us-east-1 returns None for LocationConstraint
        if region is None or region == "":
            region = "us-east-1"
        
        _bucket_regions[bucket] = region
        logging.info(f"Detected bucket '{bucket}' region: {region}")
        return region
        
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "PermanentRedirect":
            # Parse the error message to get the correct endpoint/region
            error_message = e.response.get("Error", {}).get("Message", "")
            logging.warning(f"PermanentRedirect for bucket '{bucket}': {error_message}")
            # Fall back to default region
            region = AWS_REGION
        elif error_code == "AccessDenied":
            # If we can't access, use default region
            logging.warning(f"AccessDenied when detecting region for bucket '{bucket}', using default: {AWS_REGION}")
            region = AWS_REGION
        else:
            logging.error(f"Error detecting region for bucket '{bucket}': {e}")
            region = AWS_REGION
        
        _bucket_regions[bucket] = region
        return region
    except Exception as e:
        logging.error(f"Unexpected error detecting region for bucket '{bucket}': {e}")
        region = AWS_REGION
        _bucket_regions[bucket] = region
        return region

def _get_s3_client_for_region(region: str):
    """Get or create S3 client for a specific region"""
    global _s3_clients, _cached_key_id
    
    # Invalidate all clients if credentials changed
    if _cached_key_id != AWS_ACCESS_KEY_ID:
        _s3_clients = {}
        _cached_key_id = AWS_ACCESS_KEY_ID
    
    # Return cached client for this region
    if region in _s3_clients:
        return _s3_clients[region]
    
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
    
    client = session.client(
        "s3",
        region_name=region,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        config=config,
    )
    
    _s3_clients[region] = client
    logging.debug(f"Created S3 client for region: {region}")
    return client

def presign_get(bucket: str, key: str, expires: int = 900) -> Optional[str]:
    """
    Generate a presigned URL for an S3 object.
    Auto-detects the bucket region to avoid PermanentRedirect errors.
    
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
        # Auto-detect bucket region to avoid PermanentRedirect errors
        bucket_region = _get_bucket_region(bucket)
        
        # Get S3 client for the bucket's region
        s3 = _get_s3_client_for_region(bucket_region)
        
        # Generate presigned URL
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires,
        )
        
        logging.debug(f"Generated presigned URL for bucket={bucket}, key={key}, region={bucket_region}")
        return url
        
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "PermanentRedirect":
            # Clear cached region and retry once
            logging.warning(f"PermanentRedirect for bucket '{bucket}', clearing cache and retrying")
            if bucket in _bucket_regions:
                del _bucket_regions[bucket]
            # Retry once
            try:
                bucket_region = _get_bucket_region(bucket)
                s3 = _get_s3_client_for_region(bucket_region)
                url = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": bucket, "Key": key},
                    ExpiresIn=expires,
                )
                return url
            except Exception as retry_error:
                logging.error(f"Retry failed for bucket={bucket}, key={key}: {retry_error}", exc_info=True)
                return None
        else:
            logging.error(f"ClientError generating presigned URL for bucket={bucket}, key={key}: {e}", exc_info=True)
            return None
    except Exception as e:
        logging.error(f"Error generating presigned URL for bucket={bucket}, key={key}: {e}", exc_info=True)
        return None
