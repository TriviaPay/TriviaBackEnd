# Environment Variables for Wallet + IAP

## Required Environment Variables

### Database

1. **DATABASE_URL** (Required)
   - PostgreSQL connection string
   - Format: `postgresql://user:pass@host:port/dbname`
   - Used by: Both sync and async database connections

## IAP (In-App Purchase) Configuration

### Apple IAP

2. **APPLE_IAP_SHARED_SECRET** (Required for Apple IAP)
   - App-specific shared secret from App Store Connect
   - Format: A long alphanumeric string
   - Used for: Verifying Apple App Store receipts
   - Get from: App Store Connect → Your App → App Information → App-Specific Shared Secret

3. **APPLE_IAP_USE_SANDBOX** (Optional)
   - Default: `false`
   - Set to `true` to force sandbox environment for testing
   - Used for: Overriding environment when testing Apple IAP
   - Format: `true` or `false` (case-insensitive)

4. **APPLE_APP_BUNDLE_ID** (Required for StoreKit 2 verification)
   - Your app bundle identifier (e.g., `com.triviapay.app`)
   - Used to validate signed transactions

5. **APPLE_ROOT_CERT_PATHS** (Required for StoreKit 2 verification)
   - Comma-separated paths to Apple Root CA certificates (PEM)
   - Example: `/path/to/AppleRootCA-G3.pem,/path/to/AppleRootCA-G2.pem`

### Google Play IAP

6. **GOOGLE_IAP_SERVICE_ACCOUNT_JSON** (Required for Google IAP)
   - Google Cloud service account JSON credentials
   - Can be either:
     - Path to a JSON key file: `/path/to/service-account-key.json`
     - Raw JSON content as a string
   - Used for: Authenticating with Google Play Developer API
   - Required scope: `https://www.googleapis.com/auth/androidpublisher`

7. **GOOGLE_IAP_PACKAGE_NAME** (Required for Google IAP)
   - Default: `com.triviapay.app`
   - Android app package name (e.g., `com.triviapay.app`)
   - Used for: Identifying your app in Google Play Developer API calls
   - Must match the package name in your Google Play Console

8. **GOOGLE_IAP_REFUND_NOTIFICATION_TYPES** (Optional)
   - Comma-separated integer notification types that should be treated as refunds
   - Default: `2,3,4,5`

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
# Database (already exists)
DATABASE_URL=postgresql://user:password@host:5432/dbname

# Apple IAP Configuration
APPLE_IAP_SHARED_SECRET=your_app_specific_shared_secret_here
APPLE_IAP_USE_SANDBOX=false
APPLE_APP_BUNDLE_ID=com.triviapay.app
APPLE_ROOT_CERT_PATHS=/path/to/AppleRootCA-G3.pem,/path/to/AppleRootCA-G2.pem

# Google Play IAP Configuration
# Option 1: Path to service account JSON file
GOOGLE_IAP_SERVICE_ACCOUNT_JSON=/path/to/service-account-key.json
# Option 2: Raw JSON content (escape quotes properly)
# GOOGLE_IAP_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"..."}
GOOGLE_IAP_PACKAGE_NAME=com.triviapay.app
GOOGLE_IAP_REFUND_NOTIFICATION_TYPES=2,3,4,5
```
