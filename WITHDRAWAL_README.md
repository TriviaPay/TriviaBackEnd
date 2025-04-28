# Withdrawal System Implementation

This document outlines the implementation of the withdrawal system in our application, which supports both standard and instant withdrawal methods.

## Overview

The withdrawal system allows users to transfer funds from their wallet to their bank accounts. The system supports two withdrawal methods:

1. **Standard Withdrawal**: Free, processed within 1-3 business days, requires admin approval
2. **Instant Withdrawal**: Processed immediately, incurs a 1.5% fee (minimum $0.50), no admin approval required

## Key Features

- Flexible withdrawal methods with different fee structures
- Bank account management for saving and reusing withdrawal destinations
- Admin review process for standard withdrawals
- Detailed transaction history for users and admins
- Secure storage of bank account information

## Endpoints

### User Endpoints

#### 1. Withdraw from Wallet
**Endpoint:** `POST /stripe/withdraw-from-wallet`

**Purpose:** Initiate a withdrawal from the user's wallet to their bank account

**Parameters:**
- `amount`: Amount in dollars to withdraw
- `payout_method`: Either "standard" or "instant"
- `bank_account_id`: (Optional) ID of a saved bank account to use
- `payout_details`: (Optional) Bank details if not using a saved account

**Response (Standard):**
```json
{
  "status": "pending",
  "amount": 100.00,
  "fee": 0,
  "net_amount": 100.00,
  "currency": "usd",
  "transaction_id": 123,
  "message": "Withdrawal request submitted successfully. Funds will be transferred within 1-3 business days after admin approval."
}
```

**Response (Instant):**
```json
{
  "status": "succeeded",
  "amount": 100.00,
  "fee": 1.50,
  "net_amount": 98.50,
  "currency": "usd",
  "transaction_id": 124,
  "payment_intent_id": "po_inst_1234567890",
  "message": "Instant withdrawal processed successfully. Funds should arrive within minutes."
}
```

#### 2. Withdrawal History
**Endpoint:** `GET /stripe/withdrawal-history`

**Purpose:** Retrieve a user's withdrawal history

**Parameters:**
- `limit`: Maximum number of records to return (default: 10)
- `offset`: Number of records to skip (default: 0)

**Response:**
```json
{
  "withdrawals": [
    {
      "id": 123,
      "amount": 100.00,
      "currency": "usd",
      "status": "pending",
      "created_at": "2023-06-18T11:45:32.123456",
      "updated_at": "2023-06-18T11:45:32.123456",
      "payout_method": "standard",
      "payout_details": {
        "account_name": "John Doe",
        "account_number_last4": "6789",
        "bank_name": "Chase Bank"
      }
    }
  ],
  "total_count": 1
}
```

#### 3. Manage Bank Accounts
**Endpoint:** `POST /stripe/bank-accounts`  
**Purpose:** Add a new bank account

**Endpoint:** `GET /stripe/bank-accounts`  
**Purpose:** List user's bank accounts

**Endpoint:** `DELETE /stripe/bank-accounts/{account_id}`  
**Purpose:** Delete a bank account

### Admin Endpoints

#### 1. Process Withdrawal (Admin Only)
**Endpoint:** `POST /stripe/admin/process-withdrawal/{transaction_id}`

**Purpose:** Approve or reject standard withdrawal requests

**Parameters:**
- `transaction_id`: ID of the withdrawal transaction to process
- `status`: Either "completed" or "failed"
- `notes`: Optional admin notes

**Response:**
```json
{
  "transaction_id": 123,
  "status": "completed",
  "notes": "Transferred to account ending in 6789",
  "withdrawal_type": "standard",
  "updated_at": "2023-06-20T14:30:12.123456",
  "message": "Standard withdrawal successfully completed."
}
```

#### 2. View All Withdrawals (Admin Only)
**Endpoint:** `GET /stripe/admin/withdrawals`

**Purpose:** View and filter all withdrawal transactions

**Parameters:**
- `withdrawal_type`: Filter by "standard" or "instant"
- `status`: Filter by transaction status
- `user_id`: Filter by user ID
- `limit`: Maximum records to return
- `offset`: Number of records to skip

**Response:**
```json
{
  "withdrawals": [
    {
      "id": 123,
      "user_id": "9876543210",
      "user_email": "user@example.com",
      "user_name": "John Doe",
      "amount": 100.00,
      "fee": 0,
      "net_amount": 100.00,
      "currency": "usd",
      "status": "pending",
      "payment_intent_id": null,
      "withdrawal_type": "standard",
      "bank_details": {
        "account_name": "John Doe",
        "bank_name": "Chase Bank",
        "account_last4": "6789"
      },
      "admin_notes": null,
      "last_error": null,
      "created_at": "2023-06-18T11:45:32.123456",
      "updated_at": "2023-06-18T11:45:32.123456"
    }
  ],
  "total_count": 1,
  "summary": {
    "by_type": {
      "instant": 0,
      "standard": 1
    },
    "by_status": {
      "pending": 1,
      "processing": 0,
      "succeeded": 0,
      "failed": 0
    }
  }
}
```

## Implementation Details

### Database Schema

The system uses the following database tables:

1. **payment_transactions** - Stores all withdrawal transactions
   - `id`: Primary key
   - `user_id`: User making the withdrawal
   - `amount`: Amount being withdrawn
   - `currency`: Currency of the withdrawal
   - `status`: Transaction status
   - `payment_method_type`: "standard" or "instant"
   - `payment_metadata`: JSON data including bank details, fees, etc.
   - `admin_notes`: Notes from admin review
   - `created_at`, `updated_at`: Timestamps

2. **user_bank_accounts** - Stores user's saved bank accounts
   - `id`: Primary key
   - `user_id`: User who owns the account
   - `account_name`: Name on the account
   - `account_number_last4`: Last 4 digits of account number
   - `account_number_encrypted`: Encrypted account number
   - `routing_number_encrypted`: Encrypted routing number
   - `bank_name`: Name of the bank
   - `is_default`: Whether this is the default account
   - `is_verified`: Whether the account has been verified

### Security Considerations

1. **Bank Account Information**: All sensitive bank details are encrypted in the database
2. **Admin Authorization**: Only admins can view all withdrawals and process withdrawal requests
3. **Validation**: Amount validation ensures users can't withdraw more than their balance
4. **Error Handling**: Failed withdrawals automatically refund the user's wallet

### Stripe Integration

For instant withdrawals, the system integrates with Stripe's Instant Payout feature. In production:

1. The application creates a Stripe Payout with `method: "instant"`
2. The `payout.paid` and `payout.failed` webhook events update the transaction status
3. Fees are calculated and deducted automatically

For standard withdrawals, the admin approval process:

1. Reviews the withdrawal request
2. Creates a standard Stripe Payout when approved
3. Updates the transaction status based on webhook events

## Workflow Diagrams

### Standard Withdrawal Flow

1. User initiates withdrawal → Status: "pending"
2. Admin reviews and approves → Status: "processing"
3. Stripe processes payout → Status: "succeeded"
4. If admin rejects → Status: "failed" and funds returned to wallet

### Instant Withdrawal Flow

1. User initiates withdrawal → Status: "processing"
2. Fee is calculated and deducted from withdrawal amount
3. Stripe processes instant payout → Status: "succeeded"
4. If payout fails → Status: "failed" and funds returned to wallet

## Error Handling

The system handles various error cases:

1. **Insufficient funds**: Returns 400 error with detailed message
2. **Invalid bank account**: Returns 404 if account not found or 400 if unverified
3. **Payout failure**: Automatically refunds the user's wallet
4. **Processing errors**: Logs detailed error information for troubleshooting

## Future Enhancements

Possible future enhancements include:

1. **Additional withdrawal methods**: Support for PayPal, crypto, etc.
2. **Tiered fee structure**: Lower fees for higher withdrawal amounts or premium users
3. **Scheduled withdrawals**: Allow users to schedule regular withdrawals
4. **Fraud detection**: Implement additional security checks for large withdrawals
5. **User notifications**: Email or push notifications for withdrawal status updates 