# Client-Side Token Handling Fix

We've identified an issue with the JWT token format in your application. The server is receiving tokens with incorrect formatting (5 parts instead of the standard 3-part JWT format), which is causing authentication failures, particularly for admin endpoints.

## The Issue

JWT tokens should follow a standard format of `header.payload.signature`, consisting of 3 parts separated by periods. However, your server logs show tokens being received in a format like:

```
header.payload.signature.header2.payload2.signature2
```

This suggests token concatenation is occurring somewhere in your client-side code.

## How to Fix

### 1. Check Mobile App Token Storage

If you're developing a mobile app:

```javascript
// INCORRECT WAY
// This might be concatenating tokens
const storeToken = async (newToken) => {
  try {
    // Retrieve existing token
    const existingToken = await AsyncStorage.getItem('userToken');
    // Concatenating tokens (WRONG)
    const tokenToStore = existingToken ? existingToken + newToken : newToken;
    await AsyncStorage.setItem('userToken', tokenToStore);
  } catch (error) {
    console.error('Error storing token:', error);
  }
};

// CORRECT WAY
const storeToken = async (newToken) => {
  try {
    // Just store the new token directly
    await AsyncStorage.setItem('userToken', newToken);
  } catch (error) {
    console.error('Error storing token:', error);
  }
};
```

### 2. Check Authorization Header Formation

If you're using axios or another HTTP client:

```javascript
// INCORRECT WAY
axios.interceptors.request.use(config => {
  const token = localStorage.getItem('token');
  if (token) {
    // Could lead to concatenation if token already has 'Bearer ' prefix
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// CORRECT WAY
axios.interceptors.request.use(config => {
  const token = localStorage.getItem('token');
  if (token) {
    // Remove any existing 'Bearer ' prefix to avoid duplication
    const cleanToken = token.replace(/^Bearer\s+/i, '');
    config.headers.Authorization = `Bearer ${cleanToken}`;
  }
  return config;
});
```

### 3. Check for Query Parameter Passing

If you're passing tokens via URL query parameters:

```javascript
// INCORRECT WAY
const url = `${apiUrl}/admin/trigger-draw?access_token=${token}`;

// CORRECT WAY
// First ensure token doesn't already contain query parameters
const cleanToken = token.split('?')[0]; 
const url = `${apiUrl}/admin/trigger-draw?access_token=${cleanToken}`;
```

## Implementation Steps

1. Review all client-side code that handles token storage, retrieval, and transmission
2. Check for JWT token parsing and validation in your client code
3. Implement the fixes above as appropriate
4. Test authentication flows, especially admin endpoints

## Server-Side Changes

We've already implemented a fix on the server side to handle malformed tokens, but it's better to fix the root cause in the client code.

## Testing the Fix

After implementing these changes, you should:

1. Clear all stored tokens in your client app
2. Log in again to get a fresh token
3. Try accessing protected endpoints, especially admin functions
4. Check the server logs to ensure tokens are being received in the correct format

If you continue to experience issues after implementing these fixes, please reach out for further assistance. 