# Environment Variables for New Wallet System

## Required Environment Variables

### Stripe Configuration

1. **STRIPE_API_KEY** (Required)
   - Your Stripe secret API key
   - Format: `sk_test_...` (test mode) or `sk_live_...` (production)
   - Used for: Creating Connect accounts, payouts, webhook verification
   - Get from: [Stripe Dashboard → Developers → API keys](https://dashboard.stripe.com/apikeys)

2. **STRIPE_WEBHOOK_SECRET** (Required for webhooks)
   - Webhook signing secret from Stripe
   - Format: `whsec_...`
   - Used for: Verifying webhook signatures
   - Get from: [Stripe Dashboard → Developers → Webhooks](https://dashboard.stripe.com/webhooks)
   - For local testing: Use `stripe listen --forward-to localhost:8000/api/v1/stripe/webhook` and copy the secret

3. **STRIPE_PUBLISHABLE_KEY** (Optional, for testing/frontend)
   - Stripe publishable key (safe to expose)
   - Format: `pk_test_...` or `pk_live_...`
   - Used for: Frontend Stripe.js integration, testing
   - Get from: [Stripe Dashboard → Developers → API keys](https://dashboard.stripe.com/apikeys)

### Database

4. **DATABASE_URL** (Required - already exists)
   - PostgreSQL connection string
   - Format: `postgresql://user:pass@host:port/dbname`
   - Used by: Both sync and async database connections

### Optional Stripe Connect URLs

5. **STRIPE_CONNECT_RETURN_URL** (Optional)
   - Default: `https://app.triviapay.com/onboarding/return`
   - Where users are redirected after Stripe Connect onboarding
   - Override if your app URL differs

6. **STRIPE_CONNECT_REFRESH_URL** (Optional)
   - Default: `https://app.triviapay.com/onboarding/refresh`
   - Where users are redirected if onboarding link expires
   - Override if your app URL differs

## IAP (In-App Purchase) Configuration

### Apple IAP

7. **APPLE_IAP_SHARED_SECRET** (Required for Apple IAP)
   - App-specific shared secret from App Store Connect
   - Format: A long alphanumeric string
   - Used for: Verifying Apple App Store receipts
   - Get from: [App Store Connect → Your App → App Information → App-Specific Shared Secret](https://appstoreconnect.apple.com)
   - Note: This is different from the receipt validation shared secret

8. **APPLE_IAP_USE_SANDBOX** (Optional)
   - Default: `false`
   - Set to `true` to force sandbox environment for testing
   - Used for: Overriding environment when testing Apple IAP
   - Format: `true` or `false` (case-insensitive)

### Google Play IAP

9. **GOOGLE_IAP_SERVICE_ACCOUNT_JSON** (Required for Google IAP)
   - Google Cloud service account JSON credentials
   - Can be either:
     - Path to a JSON key file: `/path/to/service-account-key.json`
     - Raw JSON content as a string
   - Used for: Authenticating with Google Play Developer API
   - Get from: [Google Cloud Console → IAM & Admin → Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts)
   - Required scope: `https://www.googleapis.com/auth/androidpublisher`
   - Instructions:
     1. Create a service account in Google Cloud Console
     2. Grant it access to your Google Play Console project
     3. Download the JSON key file
     4. Either set the path or paste the JSON content as this variable

10. **GOOGLE_IAP_PACKAGE_NAME** (Required for Google IAP)
    - Default: `com.triviapay.app`
    - Android app package name (e.g., `com.triviapay.app`)
    - Used for: Identifying your app in Google Play Developer API calls
    - Must match the package name in your Google Play Console

## IAP Products

**Note:** IAP products are looked up directly from product tables (`avatars`, `frames`, `gem_package_config`, `badges`) using the `product_id` field. The `price_minor` field in these tables determines the amount credited to the wallet when a user completes an IAP purchase.

Product IDs should match the format:
- Avatars: `AV001`, `AV002`, etc.
- Frames: `FR001`, `FR002`, etc.
- Gem Packages: `GP001`, `GP002`, etc.
- Badges: `BD001`, `BD002`, etc.

When configuring products in Apple App Store Connect or Google Play Console, use these same product IDs.

## Example .env File

```bash
# Stripe Configuration
STRIPE_API_KEY=sk_test_51AbCdEfGhIjKlMnOpQrStUvWxYz1234567890
STRIPE_WEBHOOK_SECRET=whsec_1234567890abcdefghijklmnopqrstuvwxyz
STRIPE_PUBLISHABLE_KEY=pk_test_51AbCdEfGhIjKlMnOpQrStUvWxYz1234567890

# Database (already exists)
DATABASE_URL=postgresql://user:password@host:5432/dbname

# Optional Stripe Connect URLs
STRIPE_CONNECT_RETURN_URL=https://your-app.com/onboarding/return
STRIPE_CONNECT_REFRESH_URL=https://your-app.com/onboarding/refresh

# Apple IAP Configuration
APPLE_IAP_SHARED_SECRET=your_app_specific_shared_secret_here
APPLE_IAP_USE_SANDBOX=false

# Google Play IAP Configuration
# Option 1: Path to service account JSON file
GOOGLE_IAP_SERVICE_ACCOUNT_JSON=/path/to/service-account-key.json
# Option 2: Raw JSON content (escape quotes properly)
# GOOGLE_IAP_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"..."}
GOOGLE_IAP_PACKAGE_NAME=com.triviapay.app
```

## Testing with Stripe CLI

For local webhook testing:

```bash
# Install Stripe CLI
brew install stripe/stripe-cli/stripe

# Login to Stripe
stripe login

# Forward webhooks to local server
stripe listen --forward-to localhost:8000/api/v1/stripe/webhook

# This will output a webhook secret - add it to your .env as STRIPE_WEBHOOK_SECRET
```

## Webhook Endpoint

The webhook endpoint is available at:
- **URL**: `POST /api/v1/stripe/webhook`
- **Headers**: Requires `Stripe-Signature` header
- **Events handled**:
  - `account.updated` - Connect account status updates
  - `transfer.paid` / `payout.paid` - Successful payouts
  - `transfer.failed` / `payout.failed` - Failed payouts (refunds wallet)

## Publishable Key Endpoint

Get the publishable key for frontend use:
- **URL**: `GET /api/v1/stripe/connect/publishable-key`
- **Auth**: Not required (publishable keys are safe to expose)
- **Response**: `{"publishable_key": "pk_test_..."}`

