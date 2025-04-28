# Stripe Integration Documentation

## Overview
This document outlines the integration of Stripe into the TriviaPay backend application, focusing on payment processing, subscription management, and automated withdrawals.

## Features

### Payment Processing
- **Payment Intents**: Securely collect payment information and process payments
- **Webhooks**: Automated handling of payment events (succeeded, failed, processing)
- **Error Handling**: Comprehensive error management for failed payments

### Wallet System
- **Balance Management**: Add funds, check balances, and process withdrawals
- **Transaction History**: View detailed payment history and transaction details
- **Automated Withdrawals**: Schedule and process automated withdrawals to bank accounts

### Subscriptions
- **Plan Management**: Create and manage subscription plans with different tiers
- **Recurring Billing**: Automated charging of subscription fees
- **Upgrade/Downgrade**: Support for changing subscription tiers
- **Cancellation**: Process for canceling subscriptions with proper proration

## Configuration

### Environment Variables
The following environment variables must be set to enable Stripe functionality:

```
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
```

### Webhook Setup
1. Create a webhook endpoint in the Stripe Dashboard
2. Point the webhook to your API endpoint: `https://yourdomain.com/api/stripe/webhook`
3. Select the following events to listen for:
   - `payment_intent.succeeded`
   - `payment_intent.payment_failed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`

## API Endpoints

### Payment Management
- `POST /api/stripe/create-payment-intent`: Create a new payment intent
- `GET /api/stripe/wallet/balance`: Get current wallet balance
- `POST /api/stripe/wallet/withdraw`: Request a withdrawal from wallet

### Bank Account Management
- `POST /api/stripe/bank-accounts`: Add a new bank account
- `GET /api/stripe/bank-accounts`: List user's bank accounts
- `DELETE /api/stripe/bank-accounts/{id}`: Remove a bank account

### Subscription Management
- `GET /api/stripe/subscriptions/plans`: List available subscription plans
- `POST /api/stripe/subscriptions`: Create a new subscription
- `GET /api/stripe/subscriptions`: Get current subscription status
- `PATCH /api/stripe/subscriptions/{id}`: Update subscription settings
- `DELETE /api/stripe/subscriptions/{id}`: Cancel a subscription

### Admin Endpoints
- `POST /api/stripe/admin/process-withdrawal`: Process a pending withdrawal (admin only)

## Automated Withdrawals

The system supports automated withdrawals through the following process:

1. **Request Initiation**: Users request a withdrawal via the `/api/stripe/wallet/withdraw` endpoint
2. **Validation**: System validates balance and bank account details
3. **Admin Review**: The withdrawal enters a pending state
4. **Processing**: Admins review and process the withdrawal using `/api/stripe/admin/process-withdrawal`
5. **Status Updates**: Users can track withdrawal status via the wallet transaction history

### Withdrawal Statuses
- `pending`: Initial state when withdrawal is requested
- `processing`: Admin has approved and Stripe is processing
- `completed`: Funds successfully transferred
- `failed`: Withdrawal could not be completed
- `refunded`: Failed withdrawal with amount returned to wallet

## Subscription Workflow

1. **Plan Selection**: Users browse available plans via `/api/stripe/subscriptions/plans`
2. **Subscribe**: User creates a subscription with payment method
3. **Billing**: Automated recurring billing based on subscription interval
4. **Management**: Users can manage subscription settings and cancel if needed

### Subscription Features
- **Auto-renewal**: Subscriptions automatically renew unless canceled
- **Proration**: Charges are prorated when upgrading/downgrading
- **Trial Periods**: Support for free trial periods
- **Grace Period**: Brief grace period for failed payments before cancellation

## Security Considerations

- All payment data is processed directly by Stripe, never touching our servers
- Bank account information is securely stored and encrypted by Stripe
- Only the last 4 digits of account/card numbers are stored in our database
- All webhook events are verified using the Stripe webhook signature
- Admin-only endpoints are protected with proper authentication and authorization

## Testing

Use Stripe test keys in development to simulate payments without processing real charges.
Stripe provides test card numbers and bank accounts for various scenarios:

- Test Card Success: `4242 4242 4242 4242`
- Test Card Decline: `4000 0000 0000 0002`
- Test Bank Account: Routing `110000000`, Account `000123456789`

## Troubleshooting

Common issues and solutions:

- **Webhook Verification Errors**: Ensure webhook secret key is correct
- **Payment Declined**: Check card details and balance
- **Subscription Issues**: Verify customer and payment method exist
- **Withdrawal Failures**: Confirm bank account details are valid

## References

- [Stripe API Documentation](https://stripe.com/docs/api)
- [Webhook Guide](https://stripe.com/docs/webhooks)
- [Stripe Connect](https://stripe.com/docs/connect) for marketplace payouts 