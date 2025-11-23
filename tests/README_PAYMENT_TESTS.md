# Payment Endpoints Test Suite

This directory contains comprehensive tests for all payment-related endpoints using Stripe test credentials and mock IAP data.

## Test Files

### 1. `test_wallet_endpoints.py`
Tests wallet balance and withdrawal endpoints:
- GET `/api/v1/wallet/me` - Get wallet balance
- POST `/api/v1/wallet/withdraw` - Request withdrawal (instant/standard)

**Test Scenarios:**
- ✅ Successful wallet balance retrieval
- ✅ Wallet balance with transaction history
- ✅ Successful instant withdrawal
- ✅ Withdrawal without Stripe Connect account
- ✅ Insufficient balance
- ✅ Daily instant withdrawal limit exceeded
- ✅ Instant withdrawal payout failure with refund
- ✅ Standard withdrawal creates pending request

### 2. `test_stripe_connect.py`
Tests Stripe Connect onboarding endpoints:
- POST `/api/v1/stripe/connect/create-account-link` - Create onboarding link
- POST `/api/v1/stripe/connect/refresh-account-link` - Refresh link
- GET `/api/v1/stripe/connect/publishable-key` - Get publishable key

**Test Scenarios:**
- ✅ Create account link for new user
- ✅ Create account link for existing account
- ✅ Create account link with custom URLs
- ✅ Handle Stripe API errors
- ✅ Refresh account link
- ✅ Refresh fails without account
- ✅ Get publishable key
- ✅ Publishable key not configured

### 3. `test_iap_endpoints.py`
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

### 4. `test_admin_withdrawals.py`
Tests admin withdrawal management endpoints:
- GET `/api/v1/admin/withdrawals` - List withdrawals
- POST `/api/v1/admin/withdrawals/{id}/approve` - Approve withdrawal
- POST `/api/v1/admin/withdrawals/{id}/reject` - Reject withdrawal

**Test Scenarios:**
- ✅ List withdrawals with default filter
- ✅ List withdrawals with custom filters
- ✅ Non-admin cannot access
- ✅ Approve withdrawal successfully
- ✅ Approve non-existent withdrawal fails
- ✅ Approve withdrawal with Stripe payout failure
- ✅ Reject withdrawal with refund
- ✅ Reject non-existent withdrawal fails

## Test Data

### Stripe Test Account IDs
- `acct_1032D82eZvKYlo2C` - Standard test Express account
- `acct_1H2K3L4M5N6O7P8Q` - Additional test account

### Stripe Test Payout IDs
- `po_1234567890abcdef` - Test payout ID
- `po_abcdef1234567890` - Additional test payout ID

### Stripe Test Publishable Key
- `pk_test_51234567890abcdefghijklmnopqrstuvwxyz1234567890`

### Product IDs
- `AV001` - Test avatar product
- `FR001` - Test frame product
- `GP001` - Test gem package product
- `BD001` - Test badge product

### Transaction IDs
- Apple: `1000000123456789`, `1000000987654321`
- Google: `GPA.1234-5678-9012-34567`, `GPA.9876-5432-1098-76543`

## Running Tests

### Run all payment tests:
```bash
pytest tests/test_wallet_endpoints.py tests/test_stripe_connect.py tests/test_iap_endpoints.py tests/test_admin_withdrawals.py -v
```

### Run specific test file:
```bash
pytest tests/test_wallet_endpoints.py -v
```

### Run specific test:
```bash
pytest tests/test_wallet_endpoints.py::TestWalletBalance::test_get_wallet_balance_success -v
```

### Run with coverage:
```bash
pytest tests/test_wallet_endpoints.py --cov=app/routers/wallet --cov-report=html
```

## Environment Variables for Testing

Set these in your `.env` file or test environment:

```bash
# Stripe Test Keys (use test mode keys)
STRIPE_API_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_test_...

# Database (use test database)
DATABASE_URL=postgresql://user:pass@localhost/test_db

# IAP (optional for testing)
APPLE_SHARED_SECRET=test_secret
GOOGLE_SERVICE_ACCOUNT_JSON=path/to/test-credentials.json
```

## Mocking Strategy

All tests use `unittest.mock` to:
- Mock database sessions (async)
- Mock Stripe API calls
- Mock IAP verification services
- Mock authentication dependencies

This allows tests to run without:
- Real Stripe API calls
- Real database connections
- Real IAP verification

## Notes

1. **Async Testing**: Tests use `AsyncMock` for async database operations
2. **Stripe Test Mode**: All Stripe operations use test account IDs and test keys
3. **Idempotency**: Tests verify idempotent behavior for IAP receipts
4. **Error Handling**: Tests cover all error scenarios (insufficient balance, limits, failures)
5. **Refunds**: Tests verify wallet refunds when Stripe payouts fail

## Future Enhancements

- [ ] Integration tests with real test database
- [ ] Integration tests with Stripe test mode API
- [ ] Load testing for withdrawal endpoints
- [ ] Performance benchmarks
- [ ] Security testing (SQL injection, XSS, etc.)

