# Payments/IAP Test Suite

This directory contains tests for in-app purchase (IAP) verification.

## Test Files

### 1. `test_iap_endpoints.py`
Tests In-App Purchase verification endpoints:
- POST `/api/v1/iap/apple/verify` - Verify Apple receipt
- POST `/api/v1/iap/google/verify` - Verify Google purchase
- POST `/api/v1/iap/apple/webhook` - Apple webhook (placeholder)
- POST `/api/v1/iap/google/webhook` - Google webhook (placeholder)

**Test Scenarios:**
- ✅ Successful Apple receipt verification
- ✅ Apple verification fails with invalid receipt
- ✅ Apple verification fails when product not found
- ✅ Apple verification idempotency (duplicate receipt)
- ✅ Successful Google purchase verification
- ✅ Google verification fails with invalid token
- ✅ Webhook endpoints return not_implemented

### 2. `test_iap_flow_integrity.py`
Tests IAP integrity and platform lifecycles with a real async DB:
- ✅ Idempotency (same transaction/token twice)
- ✅ Concurrency race (two requests racing)
- ✅ Duplicate webhook delivery (Apple + Google)
- ✅ Webhook before verify (refund/revoke blocks later credit)
- ✅ Mocked Apple JWS claim validation
- ✅ Mocked Google Publisher API (ack/consume behavior)

## Running Tests

### Run IAP tests:
```bash
pytest tests/test_iap_endpoints.py -v
```

### Run IAP integrity tests:
```bash
pytest tests/test_iap_flow_integrity.py -v
```

## Environment Variables for Testing

Set these in your `.env` file or test environment:

```bash
# Database (use test database)
DATABASE_URL=postgresql://user:pass@localhost/test_db

# IAP (optional for testing)
APPLE_IAP_SHARED_SECRET=test_secret
GOOGLE_IAP_SERVICE_ACCOUNT_JSON=path/to/test-credentials.json
```

## Notes

1. **Async Testing**: Tests use `AsyncMock` for async database operations
2. **Idempotency**: Tests verify idempotent behavior for IAP receipts
