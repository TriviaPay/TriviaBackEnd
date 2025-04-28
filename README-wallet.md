# TriviaPay Wallet System Guide

This guide explains how to use the wallet system in TriviaPay, which allows users to add funds to their wallet, withdraw funds, and use those funds within the app.

## Overview

The wallet system in TriviaPay enables users to:
1. Add funds to their wallet using Stripe payments
2. Check their wallet balance
3. View recent transactions
4. Withdraw funds from their wallet
5. Use wallet funds for in-app purchases

## API Endpoints

### 1. Add Funds to Wallet

**Endpoint:** `POST /stripe/add-funds-to-wallet`

**Authentication:** Requires a valid JWT token

**Request Body:**
```json
{
  "amount": 2000,
  "currency": "usd"
}
```

**Notes:**
- `amount` is in the smallest currency unit (e.g., cents for USD)
- `currency` is a 3-letter ISO currency code (default: "usd")

**Response:**
```json
{
  "clientSecret": "pi_3Nq1sE2eZvKYlo2C1eE5sxOF_secret_vg7gkSGmS4NGBtteuaRJIRnWW",
  "paymentIntentId": "pi_3Nq1sE2eZvKYlo2C1eE5sxOF",
  "amount": 2000,
  "currency": "usd"
}
```

### 2. Check Wallet Balance

**Endpoint:** `GET /stripe/wallet-balance`

**Authentication:** Requires a valid JWT token

**Response:**
```json
{
  "wallet_balance": 50.0,
  "currency": "USD",
  "last_updated": "2023-06-15T14:30:45.123456",
  "recent_transactions": [
    {
      "id": 123,
      "amount": 20.0,
      "currency": "usd",
      "created_at": "2023-06-15T14:30:45.123456",
      "payment_method_type": "card",
      "transaction_type": "wallet_deposit"
    }
  ]
}
```

### 3. Withdraw Funds from Wallet

**Endpoint:** `POST /stripe/withdraw-from-wallet`

**Authentication:** Requires a valid JWT token

**Request Body:**
```json
{
  "amount": 25.0,
  "payout_method": "bank_account",
  "payout_details": {
    "account_holder_name": "John Doe",
    "account_number": "000123456789",
    "routing_number": "110000000",
    "account_type": "checking"
  }
}
```

**Notes:**
- `amount` is in dollars (not cents)
- `payout_method` can be "bank_account" (other methods may be added in the future)
- `payout_details` contains the information needed for the payout method

**Response:**
```json
{
  "status": "pending",
  "amount": 25.0,
  "currency": "usd",
  "transaction_id": 456,
  "message": "Withdrawal request submitted successfully. Funds will be transferred within 1-3 business days."
}
```

### 4. Get Withdrawal History

**Endpoint:** `GET /stripe/withdrawal-history`

**Authentication:** Requires a valid JWT token

**Query Parameters:**
- `limit` (optional): Maximum number of records to return (default: 10)
- `offset` (optional): Number of records to skip (default: 0)

**Response:**
```json
{
  "withdrawals": [
    {
      "id": 456,
      "amount": 25.0,
      "currency": "usd",
      "status": "pending",
      "created_at": "2023-06-18T11:45:32.123456",
      "updated_at": "2023-06-18T11:45:32.123456",
      "payout_method": "bank_account",
      "payout_details": {
        "account_holder_name": "John Doe",
        "account_number": "xxxxx6789",
        "routing_number": "110000000",
        "account_type": "checking"
      }
    }
  ],
  "total_count": 1
}
```

### 5. Admin: Process Withdrawal (Admin Only)

**Endpoint:** `POST /stripe/admin/process-withdrawal/{transaction_id}`

**Authentication:** Requires a valid JWT token with admin privileges

**Request Body:**
```json
{
  "status": "completed",
  "notes": "Transferred to account ending in 6789"
}
```

**Notes:**
- `status` must be either "completed" or "failed"
- `notes` is optional but recommended for record-keeping

**Response:**
```json
{
  "transaction_id": 456,
  "status": "completed",
  "notes": "Transferred to account ending in 6789",
  "updated_at": "2023-06-20T14:30:12.123456"
}
```

## Frontend Implementation Guide

### Step 1: Installing Stripe Libraries

For React Native:
```bash
npm install @stripe/stripe-react-native
```

For React Web:
```bash
npm install @stripe/react-stripe-js @stripe/stripe-js
```

### Step 2: Setting Up Stripe in Your App

#### React Native Example:

```jsx
import { StripeProvider } from '@stripe/stripe-react-native';
import { useState, useEffect } from 'react';
import { API_URL } from '../config';

export default function App() {
  const [publishableKey, setPublishableKey] = useState('');

  useEffect(() => {
    // Fetch the publishable key from your API
    const fetchPublishableKey = async () => {
      try {
        const response = await fetch(`${API_URL}/stripe/public-key`);
        const { publishableKey } = await response.json();
        setPublishableKey(publishableKey);
      } catch (error) {
        console.error('Error fetching publishable key:', error);
      }
    };

    fetchPublishableKey();
  }, []);

  return (
    <StripeProvider publishableKey={publishableKey}>
      {/* Your app components here */}
    </StripeProvider>
  );
}
```

### Step 3: Creating a Wallet Funding Screen

#### React Native Example:

```jsx
import { useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet, Alert } from 'react-native';
import { CardField, useStripe } from '@stripe/stripe-react-native';
import { API_URL } from '../config';

export default function AddFundsScreen() {
  const { createPaymentMethod, confirmPayment } = useStripe();
  const [amount, setAmount] = useState(20); // $20 default
  const [loading, setLoading] = useState(false);

  const handlePayPress = async () => {
    try {
      setLoading(true);
      
      // 1. Create a payment intent on the server
      const response = await fetch(`${API_URL}/stripe/add-funds-to-wallet`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${userToken}` // Your user's JWT token
        },
        body: JSON.stringify({
          amount: amount * 100, // Convert to cents
          currency: 'usd'
        })
      });
      
      const { clientSecret } = await response.json();
      
      // 2. Confirm the payment with the client secret
      const { paymentIntent, error } = await confirmPayment(clientSecret, {
        type: 'Card',
      });
      
      if (error) {
        Alert.alert('Payment Failed', error.message);
      } else if (paymentIntent) {
        Alert.alert('Success', `You've successfully added $${amount} to your wallet!`);
        // Optionally refresh the wallet balance or navigate to wallet screen
      }
    } catch (error) {
      console.error('Payment error:', error);
      Alert.alert('Error', 'There was an error processing your payment.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={styles.container}>
      <Text style={styles.header}>Add Funds to Wallet</Text>
      
      <View style={styles.amountSelector}>
        {[10, 20, 50, 100].map((value) => (
          <TouchableOpacity
            key={value}
            style={[
              styles.amountButton,
              amount === value && styles.selectedAmount
            ]}
            onPress={() => setAmount(value)}
          >
            <Text style={styles.amountText}>${value}</Text>
          </TouchableOpacity>
        ))}
      </View>
      
      <Text style={styles.label}>Card Details</Text>
      <CardField
        postalCodeEnabled={true}
        placeholders={{
          number: '4242 4242 4242 4242',
        }}
        cardStyle={styles.card}
        style={styles.cardContainer}
      />
      
      <TouchableOpacity
        style={styles.payButton}
        onPress={handlePayPress}
        disabled={loading}
      >
        <Text style={styles.payButtonText}>
          {loading ? 'Processing...' : `Add $${amount} to Wallet`}
        </Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    padding: 20,
    backgroundColor: '#fff',
  },
  header: {
    fontSize: 24,
    fontWeight: 'bold',
    marginBottom: 20,
  },
  label: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 8,
    marginTop: 16,
  },
  cardContainer: {
    height: 50,
    marginBottom: 20,
  },
  card: {
    backgroundColor: '#f5f5f5',
  },
  amountSelector: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 20,
  },
  amountButton: {
    padding: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: '#ccc',
    width: 70,
    alignItems: 'center',
  },
  selectedAmount: {
    borderColor: '#4285F4',
    backgroundColor: '#E8F1FF',
  },
  amountText: {
    fontSize: 16,
    fontWeight: '600',
  },
  payButton: {
    backgroundColor: '#4285F4',
    padding: 16,
    borderRadius: 8,
    alignItems: 'center',
    marginTop: 20,
  },
  payButtonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '600',
  },
});
```

### Step 4: Creating a Withdrawal Screen

```jsx
import { useState } from 'react';
import { View, Text, TextInput, TouchableOpacity, StyleSheet, Alert, ScrollView } from 'react-native';
import { API_URL } from '../config';

export default function WithdrawFundsScreen() {
  const [amount, setAmount] = useState('');
  const [accountHolderName, setAccountHolderName] = useState('');
  const [accountNumber, setAccountNumber] = useState('');
  const [routingNumber, setRoutingNumber] = useState('');
  const [accountType, setAccountType] = useState('checking');
  const [loading, setLoading] = useState(false);

  const handleWithdraw = async () => {
    if (!amount || parseFloat(amount) <= 0) {
      Alert.alert('Error', 'Please enter a valid amount');
      return;
    }

    if (!accountHolderName || !accountNumber || !routingNumber) {
      Alert.alert('Error', 'Please fill in all bank account details');
      return;
    }

    try {
      setLoading(true);
      
      const response = await fetch(`${API_URL}/stripe/withdraw-from-wallet`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${userToken}` // Your user's JWT token
        },
        body: JSON.stringify({
          amount: parseFloat(amount),
          payout_method: 'bank_account',
          payout_details: {
            account_holder_name: accountHolderName,
            account_number: accountNumber,
            routing_number: routingNumber,
            account_type: accountType
          }
        })
      });
      
      const result = await response.json();
      
      if (response.ok) {
        Alert.alert(
          'Withdrawal Requested', 
          `Your withdrawal request of $${amount} has been submitted. ${result.message}`
        );
        
        // Clear form or navigate back to wallet screen
        setAmount('');
        setAccountHolderName('');
        setAccountNumber('');
        setRoutingNumber('');
      } else {
        Alert.alert('Error', result.detail || 'There was an error processing your withdrawal request.');
      }
    } catch (error) {
      console.error('Withdrawal error:', error);
      Alert.alert('Error', 'There was an error processing your withdrawal request.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <ScrollView style={styles.container}>
      <Text style={styles.header}>Withdraw Funds</Text>
      
      <Text style={styles.label}>Amount (USD)</Text>
      <TextInput
        style={styles.input}
        placeholder="0.00"
        keyboardType="decimal-pad"
        value={amount}
        onChangeText={setAmount}
      />
      
      <Text style={styles.sectionTitle}>Bank Account Details</Text>
      
      <Text style={styles.label}>Account Holder Name</Text>
      <TextInput
        style={styles.input}
        placeholder="John Doe"
        value={accountHolderName}
        onChangeText={setAccountHolderName}
      />
      
      <Text style={styles.label}>Account Number</Text>
      <TextInput
        style={styles.input}
        placeholder="000123456789"
        keyboardType="number-pad"
        value={accountNumber}
        onChangeText={setAccountNumber}
      />
      
      <Text style={styles.label}>Routing Number</Text>
      <TextInput
        style={styles.input}
        placeholder="110000000"
        keyboardType="number-pad"
        value={routingNumber}
        onChangeText={setRoutingNumber}
      />
      
      <Text style={styles.label}>Account Type</Text>
      <View style={styles.accountTypeContainer}>
        {['checking', 'savings'].map((type) => (
          <TouchableOpacity
            key={type}
            style={[
              styles.accountTypeButton,
              accountType === type && styles.selectedAccountType
            ]}
            onPress={() => setAccountType(type)}
          >
            <Text style={[
              styles.accountTypeText,
              accountType === type && styles.selectedAccountTypeText
            ]}>
              {type.charAt(0).toUpperCase() + type.slice(1)}
            </Text>
          </TouchableOpacity>
        ))}
      </View>
      
      <Text style={styles.disclaimer}>
        Withdrawals typically take 1-3 business days to process and appear in your bank account.
        Banking information is transmitted securely and never stored on our servers without encryption.
      </Text>
      
      <TouchableOpacity
        style={styles.withdrawButton}
        onPress={handleWithdraw}
        disabled={loading}
      >
        <Text style={styles.withdrawButtonText}>
          {loading ? 'Processing...' : 'Withdraw Funds'}
        </Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    padding: 20,
    backgroundColor: '#fff',
  },
  header: {
    fontSize: 24,
    fontWeight: 'bold',
    marginBottom: 20,
  },
  label: {
    fontSize: 16,
    fontWeight: '600',
    marginBottom: 8,
    marginTop: 16,
  },
  input: {
    borderWidth: 1,
    borderColor: '#ddd',
    borderRadius: 8,
    padding: 12,
    fontSize: 16,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '600',
    marginTop: 30,
    marginBottom: 10,
  },
  accountTypeContainer: {
    flexDirection: 'row',
    marginTop: 8,
  },
  accountTypeButton: {
    paddingVertical: 8,
    paddingHorizontal: 16,
    borderRadius: 20,
    marginRight: 10,
    backgroundColor: '#f0f0f0',
  },
  selectedAccountType: {
    backgroundColor: '#4285F4',
  },
  accountTypeText: {
    fontWeight: '500',
  },
  selectedAccountTypeText: {
    color: '#fff',
  },
  disclaimer: {
    fontSize: 12,
    color: '#666',
    marginTop: 30,
    marginBottom: 20,
    lineHeight: 18,
  },
  withdrawButton: {
    backgroundColor: '#4285F4',
    padding: 16,
    borderRadius: 8,
    alignItems: 'center',
    marginBottom: 40,
  },
  withdrawButtonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '600',
  },
});
```

### Step 5: Displaying Withdrawal History

```jsx
import { useState, useEffect } from 'react';
import { View, Text, StyleSheet, FlatList, TouchableOpacity, RefreshControl } from 'react-native';
import { API_URL } from '../config';

export default function WithdrawalHistoryScreen() {
  const [withdrawals, setWithdrawals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchWithdrawals = async () => {
    try {
      setLoading(true);
      const response = await fetch(`${API_URL}/stripe/withdrawal-history`, {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${userToken}` // Your user's JWT token
        }
      });
      
      const data = await response.json();
      setWithdrawals(data.withdrawals || []);
    } catch (error) {
      console.error('Error fetching withdrawals:', error);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    fetchWithdrawals();
  }, []);

  const onRefresh = () => {
    setRefreshing(true);
    fetchWithdrawals();
  };

  const getStatusColor = (status) => {
    switch (status) {
      case 'completed': return '#4CAF50';
      case 'failed': return '#F44336';
      default: return '#FFC107';
    }
  };

  const renderWithdrawal = ({ item }) => (
    <View style={styles.withdrawalItem}>
      <View style={styles.withdrawalHeader}>
        <Text style={styles.withdrawalAmount}>${item.amount.toFixed(2)}</Text>
        <View style={[styles.statusBadge, { backgroundColor: getStatusColor(item.status) }]}>
          <Text style={styles.statusText}>{item.status}</Text>
        </View>
      </View>
      
      <Text style={styles.withdrawalDate}>
        {new Date(item.created_at).toLocaleDateString()} at {new Date(item.created_at).toLocaleTimeString()}
      </Text>
      
      <View style={styles.bankDetails}>
        <Text style={styles.bankDetailText}>
          {item.payout_details?.account_holder_name || 'N/A'} • 
          {item.payout_details?.account_type ? ` ${item.payout_details.account_type}` : ''} • 
          Account ending in {item.payout_details?.account_number?.slice(-4) || 'xxxx'}
        </Text>
      </View>
    </View>
  );

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Withdrawal History</Text>
      
      <FlatList
        data={withdrawals}
        renderItem={renderWithdrawal}
        keyExtractor={(item) => item.id.toString()}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
          />
        }
        ListEmptyComponent={
          <View style={styles.emptyContainer}>
            <Text style={styles.emptyText}>No withdrawal history found</Text>
          </View>
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    padding: 20,
    backgroundColor: '#fff',
  },
  title: {
    fontSize: 24,
    fontWeight: 'bold',
    marginBottom: 20,
  },
  withdrawalItem: {
    padding: 15,
    backgroundColor: '#f9f9f9',
    borderRadius: 8,
    marginBottom: 15,
  },
  withdrawalHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  withdrawalAmount: {
    fontSize: 18,
    fontWeight: 'bold',
  },
  statusBadge: {
    paddingVertical: 4,
    paddingHorizontal: 8,
    borderRadius: 4,
  },
  statusText: {
    color: 'white',
    fontSize: 12,
    fontWeight: '500',
  },
  withdrawalDate: {
    color: '#666',
    marginBottom: 10,
  },
  bankDetails: {
    backgroundColor: '#eeeeee',
    padding: 10,
    borderRadius: 4,
  },
  bankDetailText: {
    fontSize: 13,
    color: '#555',
  },
  emptyContainer: {
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 50,
  },
  emptyText: {
    color: '#666',
    fontSize: 16,
  }
});
```

## Webhook Setup and Testing

For local development, you can use the Stripe CLI to forward webhook events to your local server:

```bash
stripe listen --forward-to localhost:8000/stripe/webhook
```

This will output a webhook signing secret that you should add to your .env file as `STRIPE_WEBHOOK_SECRET`.

For production, set up a webhook endpoint in the Stripe Dashboard:
- URL: `https://your-api-domain.com/stripe/webhook`
- Events to listen for: `payment_intent.succeeded`, `payment_intent.payment_failed`

## Troubleshooting

1. **Payment fails with "Authentication failed"**
   - Check that your Stripe secret key is correct in your .env file
   - Make sure you're using test card numbers in test mode (e.g., 4242 4242 4242 4242)

2. **Webhook events not being received**
   - Verify the webhook URL is publicly accessible
   - Check that the webhook secret is correctly set in your .env file
   - Ensure the events you want to listen for are selected in the Stripe Dashboard

3. **Wallet balance not updating after payment**
   - Check the logs for any errors in the webhook handler
   - Verify that the payment intent has the correct metadata with "transaction_type": "wallet_deposit"
   - Make sure the user ID in the metadata matches a valid user in your database

4. **Withdrawal requests failing**
   - Check that the user has sufficient balance for the withdrawal
   - Verify that the bank account details are valid
   - For testing, use test bank account numbers (e.g., 000123456789) 