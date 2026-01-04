# Stripe Integration for TriviaPay

This README provides information about the Stripe integration in the TriviaPay backend.

## Setup

1. Install the Stripe Python package:
   ```bash
   pip install stripe
   ```

2. Set up the required environment variables in your `.env` file:
   ```
   STRIPE_SECRET_KEY=sk_test_your_stripe_secret_key
   STRIPE_PUBLISHABLE_KEY=pk_test_your_stripe_publishable_key
   STRIPE_WEBHOOK_SECRET=whsec_your_stripe_webhook_secret
   ```

3. Create the payment_transactions table by running the migration script:
   ```bash
   python migrations/create_payment_transactions.py
   ```

## API Endpoints

### GET /stripe/public-key

Returns the Stripe publishable key for use in the frontend.

**Response:**
```json
{
  "publishableKey": "pk_test_your_stripe_publishable_key"
}
```

### POST /stripe/create-payment-intent

Creates a Stripe PaymentIntent and returns the client secret to the frontend.

**Authentication:** Requires a valid JWT token.

**Request Body:**
```json
{
  "amount": 1000,
  "currency": "usd",
  "metadata": {
    "optional_key": "optional_value"
  }
}
```

**Notes:**
- `amount` is in the smallest currency unit (e.g., cents for USD)
- `currency` is a 3-letter ISO currency code (default: "usd")
- `metadata` is optional and can contain additional information about the payment

**Response:**
```json
{
  "clientSecret": "pi_1234_secret_5678"
}
```

### POST /stripe/webhook

Handles Stripe webhook events.

**Headers:**
```
Stripe-Signature: whsec_timestamp,signature
```

**Request Body:** Raw payload from Stripe

**Response:**
```json
{
  "status": "success"
}
```

## Database Schema

The payments are tracked in the `payment_transactions` table with the following schema:

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Primary key |
| user_id | BigInteger | Foreign key to users.account_id |
| payment_intent_id | String | Stripe Payment Intent ID |
| amount | Float | Payment amount in dollars |
| currency | String | 3-letter ISO currency code |
| status | String | Payment status (succeeded, failed, processing, etc.) |
| payment_method | String | Payment method ID |
| payment_method_type | String | Type of payment method (card, bank_transfer, etc.) |
| last_error | String | Error message if payment failed |
| payment_metadata | String | JSON string of metadata |
| created_at | DateTime | Record creation timestamp |
| updated_at | DateTime | Record update timestamp |

## Frontend Integration

To use Stripe in your frontend:

1. Get the publishable key from the `/stripe/public-key` endpoint
2. Create a payment intent using the `/stripe/create-payment-intent` endpoint
3. Use the client secret with Stripe Elements or Stripe.js to complete the payment

Example React/React Native code:
```javascript
// Initialize Stripe with the publishable key
const getPublishableKey = async () => {
  const response = await fetch('/stripe/public-key');
  const data = await response.json();
  return data.publishableKey;
};

// Create a payment intent
const createPaymentIntent = async (amount, currency = 'usd') => {
  const response = await fetch('/stripe/create-payment-intent', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${accessToken}`
    },
    body: JSON.stringify({
      amount,
      currency
    })
  });
  const data = await response.json();
  return data.clientSecret;
};
```

## Webhook Setup

For local development, use Stripe CLI to forward webhook events:

```bash
stripe listen --forward-to localhost:8000/stripe/webhook
```

For production, set up a webhook endpoint in the Stripe Dashboard pointing to your production URL:
- URL: `https://your-api-domain.com/stripe/webhook`
- Events to send: `payment_intent.succeeded`, `payment_intent.payment_failed`

## Supported Payment Methods

By default, this integration supports all payment methods enabled in your Stripe account.

## Error Handling

Payment errors are logged and stored in the database for debugging and customer support. 
