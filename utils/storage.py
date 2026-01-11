import logging
import os
import time
from typing import Dict, Optional

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
# Default to us-east-2 (set AWS_REGION env var to override)
AWS_REGION = os.getenv("AWS_REGION", "us-east-2")

# Environment variables that can override S3 endpoint (check and warn if set)
_ENDPOINT_OVERRIDE_VARS = [
    "AWS_ENDPOINT_URL_S3",
    "AWS_S3_ENDPOINT",
    "AWS_ENDPOINT_URL",
    "AWS_DEFAULT_REGION",  # Can cause clients to default to us-east-1/global
]


def _extract_hostname(candidate: str) -> Optional[str]:
    """
    Best-effort extraction of a hostname from a URL or host-like string.
    Returns a normalized lowercase hostname without any port.
    """
    if not candidate:
        return None
    try:
        from urllib.parse import urlparse

        if "://" in candidate:
            parsed = urlparse(candidate)
            return parsed.hostname.lower() if parsed.hostname else None

        # Treat as host[:port][/...]
        host = candidate.split("/", 1)[0]
        # Drop potential userinfo
        host = host.rsplit("@", 1)[-1]
        # Drop port
        host = host.split(":", 1)[0]
        host = host.strip().lower()
        return host or None
    except Exception:
        return None


def _is_amazonaws_host(candidate: str) -> bool:
    host = _extract_hostname(candidate)
    return bool(host) and (host == "amazonaws.com" or host.endswith(".amazonaws.com"))


def _check_endpoint_override_env_vars():
    """
    Check for environment variables that might override S3 endpoint.
    Logs warnings if any are set that could cause issues.
    """
    problematic_vars = []
    for var in _ENDPOINT_OVERRIDE_VARS:
        value = os.getenv(var)
        if value:
            problematic_vars.append((var, value))

    if problematic_vars:
        logging.warning(
            "S3 endpoint override environment variables detected (may cause PermanentRedirect):"
        )
        for var, value in problematic_vars:
            # Mask secrets but show enough to diagnose
            masked_value = value if _is_amazonaws_host(value) else "***"
            logging.warning(f"  {var}={masked_value}")
        logging.warning(
            "These may override explicit endpoint_url settings. Consider clearing them or setting to regional endpoint."
        )


# Check on module load
_check_endpoint_override_env_vars()

_s3_clients: Dict[str, any] = {}  # Cache clients by region:addressing_style
_bucket_regions: Dict[str, str] = {}  # Cache bucket regions
_bucket_addressing_styles: Dict[str, str] = (
    {}
)  # Cache addressing style per bucket (virtual or path)
_cached_creds = None  # Tuple of (access_key_id, secret_key) for cache invalidation
_presign_cache: Dict[str, tuple[str, float]] = {}
_PRESIGN_CACHE_TTL_SECONDS = int(os.getenv("PRESIGN_CACHE_TTL_SECONDS", "300"))


def _invalidate_client(region: str, addressing_style: str = "virtual"):
    """Invalidate cached S3 client for a specific region and addressing style."""
    global _s3_clients
    cache_key = f"{region}:{addressing_style}"
    _s3_clients.pop(cache_key, None)


def _preferred_addressing_for_bucket(bucket: str) -> str:
    """
    Determine preferred addressing style for a bucket.
    Buckets with dots in the name should use path-style to avoid TLS issues.
    """
    return "path" if "." in bucket else "virtual"


def _get_presign_cache_key(bucket: str, key: str, expires: int) -> str:
    return f"{bucket}:{key}:{expires}"


def _get_cached_presign_url(bucket: str, key: str, expires: int) -> Optional[str]:
    cache_key = _get_presign_cache_key(bucket, key, expires)
    cached = _presign_cache.get(cache_key)
    if not cached:
        return None
    url, expires_at = cached
    if expires_at > time.time():
        return url
    _presign_cache.pop(cache_key, None)
    return None


def _set_cached_presign_url(bucket: str, key: str, expires: int, url: str) -> None:
    ttl = min(expires, _PRESIGN_CACHE_TTL_SECONDS)
    if ttl <= 0:
        return
    cache_key = _get_presign_cache_key(bucket, key, expires)
    _presign_cache[cache_key] = (url, time.time() + ttl)
    if len(_presign_cache) > 1000:
        now = time.time()
        for key_name, (_, expires_at) in list(_presign_cache.items()):
            if expires_at <= now:
                _presign_cache.pop(key_name, None)
        if len(_presign_cache) > 1000:
            _presign_cache.clear()


def _endpoint_for_region(region: str) -> Optional[str]:
    """
    Get the explicit regional S3 endpoint URL for a given region.
    This ensures we always use the regional endpoint, not the global one.

    Args:
        region: AWS region (e.g., 'us-east-2')

    Returns:
        Regional endpoint URL (e.g., 'https://s3.us-east-2.amazonaws.com')
    """
    if not region:
        return None
    return f"https://s3.{region}.amazonaws.com"


def _assert_client_endpoint(client, region: str):
    """
    Verify that the S3 client is using the expected regional endpoint.
    Raises RuntimeError if endpoint doesn't match expectations (critical issue).
    Logs debug info if verification succeeds.

    Args:
        client: boto3 S3 client
        region: Expected AWS region

    Raises:
        RuntimeError: If endpoint doesn't match expected region
    """
    try:
        url = getattr(client.meta, "endpoint_url", "")
        host = _extract_hostname(url)
        expected_host = f"s3.{region}.amazonaws.com"
        expected_hosts = {expected_host, f"s3.dualstack.{region}.amazonaws.com"}
        if region == "us-east-1":
            expected_hosts.add("s3.amazonaws.com")

        # Check for problematic patterns
        if not url or not host:
            raise RuntimeError("S3 client endpoint_url is empty or None")
        if host == "s3.amazonaws.com" and region != "us-east-1":
            # Global endpoint detected when we expect regional
            raise RuntimeError(
                f"S3 client using global endpoint 's3.amazonaws.com' but expected regional '{expected_host}'. "
                f"Full URL: {url} (host: {host}). This will cause PermanentRedirect errors. "
                f"Check for endpoint override environment variables or proxy issues."
            )
        if host not in expected_hosts:
            # Wrong regional endpoint
            raise RuntimeError(
                f"S3 client endpoint mismatch: got '{url}' (host: {host}) but expected one of: {sorted(expected_hosts)}. "
                f"This may cause PermanentRedirect errors."
            )

        # Success - log at debug level
        logging.debug(
            f"S3 client endpoint verified: {url} (host: {host}, region: {region})"
        )
    except RuntimeError:
        raise
    except Exception as e:
        logging.warning(f"Could not verify S3 client endpoint: {e}")
        # Don't raise on verification failure, but log it


def _assert_presigned_host(url: str, region: str):
    """
    Validate that a presigned URL uses a regional S3 host, not the global endpoint.
    Raises RuntimeError if the URL host is invalid (indicates a bug in URL generation).

    This is a defensive check to catch any case where presigned URLs might be generated
    with the wrong host (global endpoint instead of regional).

    Args:
        url: Presigned URL string to validate
        region: Expected AWS region

    Raises:
        RuntimeError: If URL host doesn't match expected regional pattern
    """
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.netloc.lower()

        # Acceptable patterns:
        # 1. Virtual-hosted style: bucket.s3.region.amazonaws.com
        # 2. Path-style: s3.region.amazonaws.com
        # 3. For us-east-1 specifically: bucket.s3.amazonaws.com or s3.amazonaws.com (global endpoint is valid for us-east-1)

        is_valid = False

        if region == "us-east-1":
            # For us-east-1, global endpoint is acceptable
            is_valid = (
                host.endswith(
                    ".s3.amazonaws.com"
                )  # bucket.s3.amazonaws.com (global us-east-1)
                or host == "s3.amazonaws.com"  # s3.amazonaws.com (global)
                or host.endswith(
                    ".s3.us-east-1.amazonaws.com"
                )  # bucket.s3.us-east-1.amazonaws.com (regional)
                or host
                == "s3.us-east-1.amazonaws.com"  # s3.us-east-1.amazonaws.com (regional)
            )
        else:
            # For all other regions, MUST use regional endpoint
            is_valid = (
                host.endswith(
                    f".s3.{region}.amazonaws.com"
                )  # bucket.s3.region.amazonaws.com
                or host == f"s3.{region}.amazonaws.com"  # s3.region.amazonaws.com
            )

        if not is_valid:
            raise RuntimeError(
                f"Presigned URL has invalid S3 host '{host}' for region '{region}'. "
                f"Expected regional endpoint (e.g., 's3.{region}.amazonaws.com' or '*.s3.{region}.amazonaws.com'). "
                f"Full URL host: {host}. This indicates a bug in presigned URL generation."
            )

        logging.debug(f"Presigned URL host validated: {host} (region: {region})")
    except RuntimeError:
        raise
    except Exception as e:
        logging.warning(f"Could not validate presigned URL host: {e}")
        # Don't raise on validation failure (might be non-S3 URL), but log it


def clear_bucket_region_cache(bucket: Optional[str] = None):
    """
    Clear cached bucket region(s) and addressing styles.
    Also invalidates related S3 clients.
    Useful when bucket region changes or to force re-detection.

    Args:
        bucket: Specific bucket to clear, or None to clear all
    """
    global _bucket_regions, _bucket_addressing_styles
    if bucket:
        # Invalidate client for this bucket's cached region/style
        old_region = _bucket_regions.get(bucket)
        old_style = _bucket_addressing_styles.get(bucket, "virtual")
        if old_region:
            _invalidate_client(old_region, old_style)
        _bucket_regions.pop(bucket, None)
        _bucket_addressing_styles.pop(bucket, None)
        logging.info(f"Cleared region and addressing style cache for bucket: {bucket}")
    else:
        # Clear all caches and clients
        _bucket_regions.clear()
        _bucket_addressing_styles.clear()
        _s3_clients.clear()
        logging.info("Cleared all bucket region and addressing style caches")


def _regional_s3_config() -> Optional[any]:
    """
    Create a Config that forces regional endpoints and prevents global endpoint usage.
    This ensures GetBucketLocation and other S3 operations never hit s3.amazonaws.com.
    """
    if Config is None:
        return None
    return Config(
        signature_version="s3v4",
        s3={
            "addressing_style": "path",  # GetBucketLocation uses path-style
            "use_accelerate_endpoint": False,
            "use_global_endpoint": False,  # CRITICAL: Prevent global endpoint usage
            "us_east_1_regional_endpoint": "regional",  # CRITICAL: Use regional us-east-1, not global
        },
    )


def _get_bucket_region(bucket: str) -> str:
    """
    Get the region for a specific bucket.
    Auto-detects the bucket region to avoid PermanentRedirect errors.
    Defaults to AWS_REGION (us-east-2) if detection fails.

    IMPORTANT: This function explicitly avoids using the global s3.amazonaws.com endpoint
    by forcing regional endpoint usage via endpoint_url and Config settings.
    """
    global _bucket_regions

    # Return cached region if available
    if bucket in _bucket_regions:
        return _bucket_regions[bucket]

    # Default to AWS_REGION (us-east-2) since user confirmed it's in us-east-2
    # Only try to detect if we don't have it cached
    if boto3 is None:
        logging.warning(
            f"boto3 not available, using default region {AWS_REGION} for bucket '{bucket}'"
        )
        _bucket_regions[bucket] = AWS_REGION
        return AWS_REGION

    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        logging.warning(
            f"AWS credentials not set, using default region {AWS_REGION} for bucket '{bucket}'"
        )
        _bucket_regions[bucket] = AWS_REGION
        return AWS_REGION

    # Try to get bucket location using a regional endpoint client
    # CRITICAL: We MUST use regional endpoint, not global s3.amazonaws.com
    try:
        session = boto3.session.Session()

        # Force regional us-east-1 endpoint (not global s3.amazonaws.com)
        # GetBucketLocation works from any regional endpoint, but us-east-1 is commonly used
        regional_config = _regional_s3_config()

        temp_client = session.client(
            "s3",
            region_name="us-east-1",  # us-east-1 is fine, but we force REGIONAL endpoint below
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            # CRITICAL: Explicitly set regional endpoint to prevent botocore from using s3.amazonaws.com
            endpoint_url="https://s3.us-east-1.amazonaws.com",
            config=regional_config,  # Additional safeguard: Config prevents global endpoint fallback
        )

        response = temp_client.get_bucket_location(Bucket=bucket)
        region = response.get("LocationConstraint")

        # us-east-1 returns None or empty string for LocationConstraint
        if region is None or region == "":
            region = "us-east-1"

        _bucket_regions[bucket] = region
        logging.info(f"Detected bucket '{bucket}' region: {region}")
        return region

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        error_message = e.response.get("Error", {}).get("Message", "")
        error_region = e.response.get("Error", {}).get("Region", "")

        # Log x-amz-bucket-region header if present (S3 tells us the actual bucket region)
        try:
            hdrs = e.response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
            amz_region = hdrs.get("x-amz-bucket-region")
            if amz_region:
                logging.warning(
                    f"S3 says bucket '{bucket}' region is: {amz_region} (via x-amz-bucket-region header) - using this for region detection"
                )
                # Use the header value directly if present
                _bucket_regions[bucket] = amz_region
                return amz_region
        except Exception:
            pass

        if error_code in ("PermanentRedirect", "AuthorizationQueryParametersError"):
            # Parse error message to extract expected region
            region = None

            # First, check if Region field is in the error response (most reliable)
            if error_region:
                region = error_region
                logging.info(f"Using region from error response Region field: {region}")

            # Otherwise, try to extract from error message
            if not region and "expecting" in error_message.lower():
                import re

                region_match = re.search(
                    r"expecting\s+['\"]?([a-z0-9-]+)['\"]?",
                    error_message,
                    re.IGNORECASE,
                )
                if region_match:
                    region = region_match.group(1)
                    logging.info(
                        f"Extracted expected region from PermanentRedirect: {region}"
                    )

            # Fall back to default if we couldn't extract region
            if not region:
                logging.warning(
                    f"PermanentRedirect/AuthorizationQueryParametersError for bucket '{bucket}': {error_message}, using default: {AWS_REGION}"
                )
                region = AWS_REGION
        elif error_code == "AccessDenied":
            # If we can't access, use default region (us-east-2 as confirmed by user)
            logging.warning(
                f"AccessDenied when detecting region for bucket '{bucket}', using default: {AWS_REGION}"
            )
            region = AWS_REGION
        else:
            logging.error(
                f"Error detecting region for bucket '{bucket}': {e}, using default: {AWS_REGION}"
            )
            region = AWS_REGION

        _bucket_regions[bucket] = region
        return region
    except Exception as e:
        logging.error(
            f"Unexpected error detecting region for bucket '{bucket}': {e}, using default: {AWS_REGION}"
        )
        region = AWS_REGION
        _bucket_regions[bucket] = region
        return region


def _get_s3_client_for_region(region: str, addressing_style: str = "virtual"):
    """Get or create S3 client for a specific region

    Args:
        region: AWS region (e.g., 'us-east-2')
        addressing_style: 'virtual' (bucket.s3.region.amazonaws.com) or 'path' (s3.region.amazonaws.com/bucket)
    """
    global _s3_clients, _cached_creds

    # Cache key includes both region and addressing style
    cache_key = f"{region}:{addressing_style}"

    # Invalidate all clients if credentials changed (track access key and secret)
    creds_tuple = (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
    if _cached_creds != creds_tuple:
        _s3_clients = {}
        _cached_creds = creds_tuple

    # Return cached client for this region and style
    if cache_key in _s3_clients:
        return _s3_clients[cache_key]

    if boto3 is None:
        raise RuntimeError("boto3 is required for presigning S3 URLs")
    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        raise RuntimeError("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set")

    session = boto3.session.Session()

    # Force signature version 4 (AWS4-HMAC-SHA256) - required by modern S3
    # Use specified addressing style
    # IMPORTANT: use_accelerate_endpoint=False ensures we don't use Transfer Acceleration
    #            which would change the endpoint to s3-accelerate.amazonaws.com
    # CRITICAL: use_global_endpoint=False and us_east_1_regional_endpoint='regional' prevent
    #           botocore from ever falling back to the global s3.amazonaws.com endpoint
    config = (
        Config(
            signature_version="s3v4",
            s3={
                "addressing_style": addressing_style,
                "use_accelerate_endpoint": False,  # Must be False to avoid accelerate endpoint
                "use_dualstack_endpoint": False,  # Must be False to avoid dualstack endpoint
                "use_global_endpoint": False,  # CRITICAL: Prevent global endpoint fallback
                "us_east_1_regional_endpoint": "regional",  # CRITICAL: Use regional us-east-1, not global
            },
        )
        if Config
        else None
    )

    # Hard-pin the regional endpoint URL to prevent hitting global endpoint
    # This overrides any environment variable settings (AWS_ENDPOINT_URL_S3, etc.)
    endpoint_url = _endpoint_for_region(region)

    if not endpoint_url:
        raise RuntimeError(f"Cannot create S3 client: invalid region '{region}'")

    # Build client with credentials
    client_params = {
        "service_name": "s3",
        "region_name": region,
        "aws_access_key_id": AWS_ACCESS_KEY_ID,
        "aws_secret_access_key": AWS_SECRET_ACCESS_KEY,
        "config": config,
    }

    # Explicitly set endpoint_url to ensure we use regional endpoint
    # This MUST be set to override any environment variables (AWS_ENDPOINT_URL_S3, etc.)
    # that might point to the global endpoint
    client_params["endpoint_url"] = endpoint_url

    # Create client - endpoint_url parameter should override any env var defaults
    client = session.client(**client_params)

    # CRITICAL: Verify the endpoint URL matches expectations immediately after creation
    # This will raise RuntimeError if something overrode our endpoint_url setting
    _assert_client_endpoint(client, region)

    # Log the actual endpoint in use (helpful for debugging)
    actual_endpoint = getattr(client.meta, "endpoint_url", "unknown")
    logging.debug(
        f"Created S3 client for region: {region}, addressing_style: {addressing_style}, endpoint: {actual_endpoint}"
    )

    _s3_clients[cache_key] = client
    return client


def presign_get(bucket: str, key: str, expires: int = 900) -> Optional[str]:
    """
    Generate a presigned URL for an S3 object.
    Auto-detects the bucket region to avoid PermanentRedirect errors.

    Args:
        bucket: S3 bucket name
        key: S3 object key (path)
        expires: URL expiration time in seconds (default 15 minutes, max 7 days = 604800)

    Returns:
        Presigned URL string or None if generation fails
    """
    if not bucket or not key:
        return None
    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        logging.warning(
            "AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY not set - cannot generate presigned URLs"
        )
        return None

    cached_url = _get_cached_presign_url(bucket, key, expires)
    if cached_url:
        return cached_url

    # Normalize key: strip leading slash to avoid // in URLs
    if key.startswith("/"):
        key = key[1:]
        logging.debug(f"Normalized key by removing leading slash: {key}")

    # Validate expires (AWS SigV4 max is 7 days = 604800 seconds)
    max_expires = 604800  # 7 days
    if expires > max_expires:
        logging.warning(
            f"Expires {expires} exceeds AWS max {max_expires}, clamping to {max_expires}"
        )
        expires = max_expires

    try:
        # Auto-detect bucket region to avoid PermanentRedirect errors (allow override for performance)
        assumed_region = os.getenv("S3_PRESIGN_ASSUME_REGION")
        if assumed_region:
            bucket_region = assumed_region
            _bucket_regions[bucket] = assumed_region
        else:
            bucket_region = _get_bucket_region(bucket)

        # Determine addressing style: use cached style or preferred style for this bucket
        addressing_style = _bucket_addressing_styles.get(
            bucket, _preferred_addressing_for_bucket(bucket)
        )

        s3 = _get_s3_client_for_region(bucket_region, addressing_style)

        # CRITICAL: Verify endpoint before generating URL - raise error if wrong
        # This catches environment variable overrides, proxy issues, or SDK misconfigurations
        _assert_client_endpoint(s3, bucket_region)

        # Double-check the endpoint right before presigning (defensive)
        final_endpoint = getattr(s3.meta, "endpoint_url", "")
        if bucket_region == "us-east-2":
            final_host = _extract_hostname(final_endpoint)
            allowed_hosts = {
                "s3.us-east-2.amazonaws.com",
                "s3.dualstack.us-east-2.amazonaws.com",
            }
            if not final_host or final_host not in allowed_hosts:
                raise RuntimeError(
                    f"Endpoint verification failed immediately before presigning: {final_endpoint} "
                    f"(host: {final_host}, expected one of: {sorted(allowed_hosts)}). "
                    "This indicates an endpoint override or SDK misconfiguration."
                )

        # Generate presigned URL
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires,
        )

        # CRITICAL: Validate the presigned URL host to ensure it's regional (not global)
        # This catches any bugs in URL generation before the URL is returned to clients
        _assert_presigned_host(url, bucket_region)

        # Log the generated URL's host for debugging (without query params)
        try:
            from urllib.parse import urlparse

            parsed = urlparse(url)
            host = parsed.hostname.lower() if parsed.hostname else ""
            logging.debug(
                f"Generated presigned URL for bucket={bucket}, key={key}, region={bucket_region}, style={addressing_style}, host={host}"
            )

            # Warn if we detect global endpoint in generated URL (should never happen with our fixes)
            is_global_host = host == "s3.amazonaws.com" or host.endswith(
                ".s3.amazonaws.com"
            )
            if bucket_region != "us-east-1" and is_global_host:
                logging.error(
                    f"WARNING: Presigned URL contains global endpoint in host '{host}' for non-us-east-1 region '{bucket_region}'. "
                    f"This should never happen - investigate immediately!"
                )
        except Exception:
            logging.debug(
                f"Generated presigned URL for bucket={bucket}, key={key}, region={bucket_region}, style={addressing_style}"
            )

        _set_cached_presign_url(bucket, key, expires, url)
        return url

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        error_message = e.response.get("Error", {}).get("Message", "")
        error_region = e.response.get("Error", {}).get("Region", "")

        # Log x-amz-bucket-region header if present (S3 tells us the actual bucket region)
        try:
            hdrs = e.response.get("ResponseMetadata", {}).get("HTTPHeaders", {})
            amz_region = hdrs.get("x-amz-bucket-region")
            if amz_region:
                logging.warning(
                    f"S3 says bucket '{bucket}' region is: {amz_region} (via x-amz-bucket-region header)"
                )
        except Exception:
            pass

        if error_code in ("PermanentRedirect", "AuthorizationQueryParametersError"):
            # Force fresh region detection before falling back
            # This ensures we get the correct region from S3 if detection was wrong
            old_region = _bucket_regions.pop(bucket, None)
            old_style = _bucket_addressing_styles.get(bucket, "virtual")

            # Invalidate old client to force rebuild
            if old_region:
                _invalidate_client(old_region, old_style)

            endpoint_hint = e.response.get("Error", {}).get("Endpoint", "")
            bucket_region = None
            addressing_style = "virtual"

            # Try to re-detect region from S3
            try:
                detected_region = _get_bucket_region(bucket)
                if detected_region:
                    bucket_region = detected_region
                    logging.info(
                        f"Redetected region after redirect error: {bucket_region}"
                    )
            except Exception as detect_error:
                logging.warning(f"Region re-detection failed: {detect_error}")

            # Check if error specifies s3.amazonaws.com (global endpoint format)
            if (
                endpoint_hint == "s3.amazonaws.com"
                and error_code == "PermanentRedirect"
            ):
                # PermanentRedirect with global endpoint hint suggests wrong endpoint was used
                # User confirmed bucket is in us-east-2, try path-style addressing
                if not bucket_region:
                    bucket_region = (
                        AWS_REGION  # Use default (us-east-2) - user confirmed this
                    )
                addressing_style = (
                    "path"  # Path-style: s3.us-east-2.amazonaws.com/avatars/key
                )
                logging.info(
                    f"PermanentRedirect with global endpoint hint for bucket '{bucket}', trying path-style addressing with region {bucket_region}"
                )
            else:
                # For AuthorizationQueryParametersError, extract region from error response
                if not bucket_region and error_region:
                    bucket_region = error_region
                    logging.info(
                        f"Using region from error response Region field: {bucket_region}"
                    )

                # Otherwise, try to extract from error message
                if not bucket_region and "expecting" in error_message.lower():
                    import re

                    region_match = re.search(
                        r"expecting\s+['\"]?([a-z0-9-]+)['\"]?",
                        error_message,
                        re.IGNORECASE,
                    )
                    if region_match:
                        bucket_region = region_match.group(1)
                        logging.info(
                            f"Extracted expected region from error message: {bucket_region}"
                        )

                # Fall back to default if we couldn't extract region
                if not bucket_region:
                    bucket_region = AWS_REGION
                    logging.warning(
                        f"Could not extract region from error, using default: {bucket_region}"
                    )

            # Cache new region and addressing style, then retry
            try:
                _bucket_regions[bucket] = bucket_region
                _bucket_addressing_styles[bucket] = addressing_style
                s3 = _get_s3_client_for_region(bucket_region, addressing_style)

                # Verify endpoint before generating URL
                _assert_client_endpoint(s3, bucket_region)

                # Final endpoint check before presigning
                final_endpoint = getattr(s3.meta, "endpoint_url", "")
                if (
                    bucket_region == "us-east-2"
                    and "s3.us-east-2.amazonaws.com" not in final_endpoint
                ):
                    raise RuntimeError(f"Endpoint mismatch in retry: {final_endpoint}")

                url = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": bucket, "Key": key},
                    ExpiresIn=expires,
                )

                # Validate the presigned URL host after retry
                _assert_presigned_host(url, bucket_region)

                logging.info(
                    f"Successfully generated presigned URL after correction: bucket={bucket}, region={bucket_region}, style={addressing_style}"
                )
                return url
            except Exception as retry_error:
                # If path-style didn't work, try virtual style with detected region
                if addressing_style == "path":
                    logging.info(
                        f"Path-style failed, trying virtual-hosted style with {bucket_region}"
                    )
                    try:
                        _invalidate_client(
                            bucket_region, "path"
                        )  # Clear failed path-style client
                        s3 = _get_s3_client_for_region(bucket_region, "virtual")

                        # Verify endpoint before generating URL
                        _assert_client_endpoint(s3, bucket_region)

                        # Final endpoint check before presigning
                        final_endpoint = getattr(s3.meta, "endpoint_url", "")
                        if (
                            bucket_region == "us-east-2"
                            and "s3.us-east-2.amazonaws.com" not in final_endpoint
                        ):
                            raise RuntimeError(
                                f"Endpoint mismatch in retry: {final_endpoint}"
                            )

                        url = s3.generate_presigned_url(
                            "get_object",
                            Params={"Bucket": bucket, "Key": key},
                            ExpiresIn=expires,
                        )

                        # Validate the presigned URL host after retry
                        _assert_presigned_host(url, bucket_region)

                        # Cache virtual style if it works
                        _bucket_addressing_styles[bucket] = "virtual"
                        logging.info(
                            f"Successfully generated presigned URL with virtual-hosted style: bucket={bucket}, region={bucket_region}"
                        )
                        return url
                    except Exception as second_retry_error:
                        logging.error(
                            f"Both addressing styles failed for bucket={bucket}, key={key}: {second_retry_error}",
                            exc_info=True,
                        )
                        return None
                else:
                    logging.error(
                        f"Retry failed for bucket={bucket}, key={key}: {retry_error}",
                        exc_info=True,
                    )
                    return None
        else:
            logging.error(
                f"ClientError generating presigned URL for bucket={bucket}, key={key}: {e}",
                exc_info=True,
            )
            return None
    except Exception as e:
        logging.error(
            f"Error generating presigned URL for bucket={bucket}, key={key}: {e}",
            exc_info=True,
        )
        return None


def upload_file(
    bucket: str, key: str, file_content: bytes, content_type: str = None
) -> bool:
    """
    Upload a file to S3.
    Auto-detects the bucket region to avoid PermanentRedirect errors.

    Args:
        bucket: S3 bucket name
        key: S3 object key (path)
        file_content: File content as bytes
        content_type: MIME type of the file (e.g., 'image/png', 'image/jpeg')

    Returns:
        True if upload succeeded, False otherwise
    """
    if not bucket or not key:
        logging.error("Bucket and key are required for upload")
        return False
    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        logging.error(
            "AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY not set - cannot upload to S3"
        )
        return False

    # Normalize key: strip leading slash to avoid // in URLs
    if key.startswith("/"):
        key = key[1:]
        logging.debug(f"Normalized key by removing leading slash: {key}")

    try:
        # Auto-detect bucket region to avoid PermanentRedirect errors
        bucket_region = _get_bucket_region(bucket)

        # Determine addressing style: use cached style or preferred style for this bucket
        addressing_style = _bucket_addressing_styles.get(
            bucket, _preferred_addressing_for_bucket(bucket)
        )

        s3 = _get_s3_client_for_region(bucket_region, addressing_style)

        # Verify endpoint before uploading
        _assert_client_endpoint(s3, bucket_region)

        # Prepare upload parameters
        upload_params = {
            "Bucket": bucket,
            "Key": key,
            "Body": file_content,
        }

        # Add content type if provided
        if content_type:
            upload_params["ContentType"] = content_type

        # Upload file
        s3.put_object(**upload_params)

        logging.info(
            f"Successfully uploaded file to S3: bucket={bucket}, key={key}, region={bucket_region}"
        )
        return True

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        error_message = e.response.get("Error", {}).get("Message", "")

        logging.error(
            f"ClientError uploading file to bucket={bucket}, key={key}: {error_code} - {error_message}"
        )
        return False
    except Exception as e:
        logging.error(
            f"Error uploading file to bucket={bucket}, key={key}: {e}", exc_info=True
        )
        return False


def delete_file(bucket: str, key: str) -> bool:
    """
    Delete a file from S3.
    Auto-detects the bucket region to avoid PermanentRedirect errors.

    Args:
        bucket: S3 bucket name
        key: S3 object key (path)

    Returns:
        True if deletion succeeded or file doesn't exist, False on error
    """
    if not bucket or not key:
        logging.error("Bucket and key are required for deletion")
        return False
    if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
        logging.error(
            "AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY not set - cannot delete from S3"
        )
        return False

    # Normalize key: strip leading slash to avoid // in URLs
    if key.startswith("/"):
        key = key[1:]
        logging.debug(f"Normalized key by removing leading slash: {key}")

    try:
        # Auto-detect bucket region to avoid PermanentRedirect errors
        bucket_region = _get_bucket_region(bucket)

        # Determine addressing style: use cached style or preferred style for this bucket
        addressing_style = _bucket_addressing_styles.get(
            bucket, _preferred_addressing_for_bucket(bucket)
        )

        s3 = _get_s3_client_for_region(bucket_region, addressing_style)

        # Verify endpoint before deleting
        _assert_client_endpoint(s3, bucket_region)

        # Delete file
        s3.delete_object(Bucket=bucket, Key=key)

        logging.info(
            f"Successfully deleted file from S3: bucket={bucket}, key={key}, region={bucket_region}"
        )
        return True

    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        error_message = e.response.get("Error", {}).get("Message", "")

        # NoSuchKey is not an error - file doesn't exist, which is fine
        if error_code == "NoSuchKey":
            logging.debug(f"File does not exist in S3: bucket={bucket}, key={key}")
            return True

        logging.error(
            f"ClientError deleting file from bucket={bucket}, key={key}: {error_code} - {error_message}"
        )
        return False
    except Exception as e:
        logging.error(
            f"Error deleting file from bucket={bucket}, key={key}: {e}", exc_info=True
        )
        return False
