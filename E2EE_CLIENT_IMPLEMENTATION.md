# E2EE Client Implementation Guide

This document provides a comprehensive guide for implementing the client-side end-to-end encryption (E2EE) functionality for private direct messages.

## Overview

The E2EE system uses **X3DH + Double Ratchet** protocol (Signal-style) for secure messaging. All encryption/decryption happens client-side. The server only handles:
- Public key distribution
- Ciphertext relay
- Message metadata (delivery/read receipts)

## Architecture

### Key Components

1. **Key Management**: Identity keys, signed prekeys, one-time prekeys
2. **Session Management**: X3DH for initial session setup, Double Ratchet for ongoing messages
3. **Message Encryption**: Client-side encryption before sending
4. **Message Decryption**: Client-side decryption after receiving
5. **Device Management**: Multi-device support with per-device sessions

## Recommended Libraries

### Web (JavaScript/TypeScript)
- **`@privacyresearchgroup/libsignal-protocol-typescript`** - TypeScript implementation of Signal Protocol
- **`libsignal-protocol-javascript`** - JavaScript implementation (older, but stable)

### iOS (Swift)
- **`libsignal-protocol-swift`** - Swift implementation
- **`SignalProtocolKit`** - Alternative Swift library

### Android (Kotlin/Java)
- **`libsignal-protocol-android`** - Android implementation
- **`Signal-Android`** - Full Signal app (reference implementation)

## Required Client Functions

### 1. Key Generation

```typescript
// Generate identity keypair (long-term)
function generateIdentityKeyPair(): KeyPair {
  // Generate Curve25519 keypair
  // Store private key securely (never send to server)
  // Return: { publicKey, privateKey }
}

// Generate signed prekey
function generateSignedPrekey(identityPrivateKey: PrivateKey): SignedPreKey {
  // Generate Curve25519 keypair
  // Sign public key with identity private key (Ed25519)
  // Return: { keyPair, signature }
}

// Generate one-time prekeys (pool of ~100)
function generateOneTimePrekeys(count: number): OneTimePreKey[] {
  // Generate array of Curve25519 keypairs
  // Return: [{ publicKey, privateKey }, ...]
}
```

### 2. Key Bundle Upload

```typescript
async function uploadKeyBundle(
  deviceId: string,
  deviceName: string,
  identityKeyPub: string,      // Base64
  signedPrekeyPub: string,     // Base64
  signedPrekeySig: string,     // Base64
  oneTimePrekeys: string[]     // Base64 array
): Promise<{ device_id, bundle_version, prekeys_stored }> {
  const response = await fetch('/e2ee/keys/upload', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${jwtToken}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      device_id: deviceId,
      device_name: deviceName,
      identity_key_pub: identityKeyPub,
      signed_prekey_pub: signedPrekeyPub,
      signed_prekey_sig: signedPrekeySig,
      one_time_prekeys: oneTimePrekeys.map(pub => ({ prekey_pub: pub }))
    })
  });
  return await response.json();
}
```

### 3. Key Bundle Fetch

```typescript
async function fetchKeyBundle(userId: number): Promise<DeviceBundle[]> {
  const response = await fetch(`/e2ee/keys/bundle?user_id=${userId}`, {
    headers: { 'Authorization': `Bearer ${jwtToken}` }
  });
  const data = await response.json();
  return data.devices; // Array of device bundles
}
```

### 4. X3DH Session Bootstrap

```typescript
async function bootstrapSessionWithX3DH(
  recipientUserId: number,
  recipientDeviceBundle: DeviceBundle
): Promise<SessionId> {
  // 1. Fetch recipient's key bundle (if not already fetched)
  // 2. Claim an OTPK from server
  const claimResponse = await fetch('/e2ee/prekeys/claim', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${jwtToken}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      device_id: recipientDeviceBundle.device_id,
      prekey_id: recipientDeviceBundle.available_prekey_id
    })
  });
  
  // 3. Perform X3DH key agreement
  // - Use: A.identity, A.ephemeral, B.identity_pub, B.signed_prekey_pub, B.otpk_pub
  // - Derive shared secret via HKDF-SHA256
  // - Seed Double Ratchet with shared secret
  
  // 4. Store session state locally
  const sessionId = generateSessionId(recipientUserId, recipientDeviceBundle.device_id);
  await storeSession(sessionId, ratchetState);
  
  return sessionId;
}
```

### 5. Double Ratchet Encrypt

```typescript
async function encryptWithDoubleRatchet(
  sessionId: SessionId,
  plaintext: string
): Promise<EncryptedMessage> {
  // 1. Load session state
  const session = await loadSession(sessionId);
  
  // 2. Ratchet forward (update sending chain)
  const { messageKey, header } = session.ratchetEncrypt(plaintext);
  
  // 3. Encrypt with XChaCha20-Poly1305
  const ciphertext = encrypt(messageKey, plaintext, header);
  
  // 4. Update session state
  await saveSession(sessionId, session);
  
  return {
    ciphertext: base64Encode(ciphertext),
    proto: 1, // DR message
    header: base64Encode(header)
  };
}
```

### 6. Double Ratchet Decrypt

```typescript
async function decryptWithDoubleRatchet(
  sessionId: SessionId,
  ciphertextEnvelope: EncryptedMessage
): Promise<string> {
  // 1. Load session state
  const session = await loadSession(sessionId);
  
  // 2. Decode ciphertext and header
  const ciphertext = base64Decode(ciphertextEnvelope.ciphertext);
  const header = base64Decode(ciphertextEnvelope.header);
  
  // 3. Ratchet and decrypt
  const messageKey = session.ratchetDecrypt(header);
  const plaintext = decrypt(messageKey, ciphertext, header);
  
  // 4. Update session state
  await saveSession(sessionId, session);
  
  return plaintext;
}
```

### 7. Send Message

```typescript
async function sendMessage(
  conversationId: string,
  plaintext: string,
  recipientDeviceIds: string[]
): Promise<void> {
  // 1. Get or create conversation
  const conversation = await getConversation(conversationId);
  
  // 2. Encrypt separately for each recipient device (multi-device)
  const encryptedMessages = await Promise.all(
    recipientDeviceIds.map(async (deviceId) => {
      const sessionId = await getOrCreateSession(conversation.peerUserId, deviceId);
      return await encryptWithDoubleRatchet(sessionId, plaintext);
    })
  );
  
  // 3. Send each encrypted message (or combine if protocol supports)
  for (const encrypted of encryptedMessages) {
    await fetch(`/dm/conversations/${conversationId}/messages`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${jwtToken}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        client_message_id: generateClientMessageId(),
        ciphertext: encrypted.ciphertext,
        proto: encrypted.proto,
        recipient_device_ids: recipientDeviceIds
      })
    });
  }
}
```

### 8. Receive Message (SSE)

```typescript
function connectToDMSSE(jwtToken: string): EventSource {
  const eventSource = new EventSource(`/dm/sse?token=${jwtToken}`);
  
  eventSource.onmessage = async (event) => {
    const data = JSON.parse(event.data);
    
    if (data.type === 'dm') {
      // 1. Find or create session
      const sessionId = await getOrCreateSession(
        data.sender_user_id,
        data.sender_device_id
      );
      
      // 2. Decrypt message
      const plaintext = await decryptWithDoubleRatchet(sessionId, {
        ciphertext: data.ciphertext,
        proto: data.proto
      });
      
      // 3. Display message in UI
      displayMessage(data.conversation_id, plaintext);
      
      // 4. Mark as delivered
      await fetch(`/dm/messages/${data.message_id}/delivered`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${jwtToken}` }
      });
    }
  };
  
  return eventSource;
}
```

### 9. Safety Number Computation

```typescript
function computeSafetyNumber(
  myIdentityKey: PublicKey,
  peerIdentityKey: PublicKey
): string {
  // Concatenate both identity keys (sorted)
  const combined = [
    myIdentityKey,
    peerIdentityKey
  ].sort().join('');
  
  // Hash with SHA-256
  const hash = sha256(combined);
  
  // Convert to numeric code (first 12 digits)
  return hashToNumericCode(hash);
}

// Display as QR code or numeric code for verification
function displaySafetyNumber(safetyNumber: string): void {
  // Show QR code or 12-digit number
  // User compares with peer to verify identity
}
```

### 10. Device Management

```typescript
// List user's devices
async function listDevices(): Promise<Device[]> {
  const response = await fetch('/e2ee/devices', {
    headers: { 'Authorization': `Bearer ${jwtToken}` }
  });
  return (await response.json()).devices;
}

// Revoke a device
async function revokeDevice(deviceId: string, reason?: string): Promise<void> {
  await fetch('/e2ee/devices/revoke', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${jwtToken}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ device_id: deviceId, reason })
  });
}
```

### 11. Key Rotation

```typescript
// Rotate signed prekey (every 60 days)
async function rotateSignedPrekeyIfDue(): Promise<void> {
  const lastRotation = await getLastPrekeyRotation();
  const daysSinceRotation = (Date.now() - lastRotation) / (1000 * 60 * 60 * 24);
  
  if (daysSinceRotation >= 60) {
    const newSignedPrekey = generateSignedPrekey(identityPrivateKey);
    await uploadKeyBundle(/* include new signed prekey */);
    await setLastPrekeyRotation(Date.now());
  }
}

// Replenish one-time prekeys (when pool < 20)
async function replenishOneTimePrekeys(): Promise<void> {
  const bundle = await fetchKeyBundle(currentUserId);
  const availablePrekeys = bundle.prekeys_available;
  
  if (availablePrekeys < 20) {
    const newPrekeys = generateOneTimePrekeys(100);
    await uploadKeyBundle(/* include new prekeys */);
  }
}
```

### 12. Attachment Encryption

```typescript
async function encryptAttachment(file: File): Promise<AttachmentDescriptor> {
  // 1. Generate random 256-bit key and nonce
  const key = generateRandomKey(32); // 256 bits
  const nonce = generateRandomNonce(24); // 192 bits for XChaCha20
  
  // 2. Read file as ArrayBuffer
  const fileData = await file.arrayBuffer();
  
  // 3. Encrypt with XChaCha20-Poly1305
  const encrypted = await encryptXChaCha20Poly1305(
    key,
    nonce,
    new Uint8Array(fileData)
  );
  
  // 4. Compute SHA-256 hash
  const sha256 = await computeSHA256(encrypted);
  
  // 5. Upload encrypted blob to S3 (get pre-signed URL)
  const uploadUrl = await getPresignedUploadURL();
  await fetch(uploadUrl, {
    method: 'PUT',
    body: encrypted
  });
  
  // 6. Return descriptor (to be sent in encrypted message)
  return {
    object_url: uploadUrl.split('?')[0], // Base URL without query params
    file_key: base64Encode(key),
    nonce: base64Encode(nonce),
    mime: file.type,
    size: file.size,
    sha256: base64Encode(sha256)
  };
}

async function decryptAttachment(descriptor: AttachmentDescriptor): Promise<Blob> {
  // 1. Download encrypted blob
  const response = await fetch(descriptor.object_url);
  const encrypted = await response.arrayBuffer();
  
  // 2. Verify SHA-256
  const computedHash = await computeSHA256(new Uint8Array(encrypted));
  if (base64Encode(computedHash) !== descriptor.sha256) {
    throw new Error('Attachment integrity check failed');
  }
  
  // 3. Decrypt
  const key = base64Decode(descriptor.file_key);
  const nonce = base64Decode(descriptor.nonce);
  const decrypted = await decryptXChaCha20Poly1305(
    key,
    nonce,
    new Uint8Array(encrypted)
  );
  
  // 4. Return as Blob
  return new Blob([decrypted], { type: descriptor.mime });
}
```

### 13. Key Backup (Optional)

```typescript
async function backupKeys(passphrase: string): Promise<void> {
  // 1. Collect all private keys
  const keys = {
    identityPrivateKey,
    signedPrekeyPrivateKey,
    oneTimePrekeyPrivateKeys,
    sessionStates
  };
  
  // 2. Derive backup key from passphrase (Argon2id)
  const backupKey = await argon2id(passphrase, {
    memoryCost: 65536, // 64 MB
    timeCost: 3,
    parallelism: 4
  });
  
  // 3. Encrypt keys with backup key
  const encrypted = await encryptAES256GCM(backupKey, JSON.stringify(keys));
  
  // 4. Upload to server (server never sees passphrase)
  await fetch('/e2ee/keys/backup', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${jwtToken}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      encrypted_backup: base64Encode(encrypted)
    })
  });
}

async function restoreKeys(passphrase: string): Promise<void> {
  // 1. Download encrypted backup
  const response = await fetch('/e2ee/keys/backup', {
    headers: { 'Authorization': `Bearer ${jwtToken}` }
  });
  const { encrypted_backup } = await response.json();
  
  // 2. Derive backup key from passphrase
  const backupKey = await argon2id(passphrase, {
    memoryCost: 65536,
    timeCost: 3,
    parallelism: 4
  });
  
  // 3. Decrypt and restore keys
  const decrypted = await decryptAES256GCM(
    backupKey,
    base64Decode(encrypted_backup)
  );
  const keys = JSON.parse(decrypted);
  
  // 4. Restore to local storage
  await restoreKeysToStorage(keys);
}
```

## Implementation Flow

### Initial Setup (First Launch)

1. Generate identity keypair
2. Generate signed prekey (signed with identity key)
3. Generate pool of 100 one-time prekeys
4. Upload key bundle to server
5. Store private keys securely (Keychain/Secure Enclave/Encrypted storage)

### Starting a Conversation

1. User selects peer user
2. Fetch peer's key bundles (`GET /e2ee/keys/bundle?user_id=X`)
3. For each peer device:
   - Claim an OTPK (`POST /e2ee/prekeys/claim`)
   - Perform X3DH key agreement
   - Initialize Double Ratchet session
4. Create/find conversation (`POST /dm/conversations`)
5. Ready to send messages

### Sending a Message

1. User types message
2. For each recipient device:
   - Load session state
   - Encrypt with Double Ratchet
3. Send encrypted message(s) (`POST /dm/conversations/{id}/messages`)
4. Server stores ciphertext and publishes to Redis
5. Recipient receives via SSE and decrypts

### Receiving a Message

1. SSE delivers ciphertext event
2. Find or create session for sender device
3. Decrypt with Double Ratchet
4. Display in UI
5. Mark as delivered (`POST /dm/messages/{id}/delivered`)
6. When user views, mark as read (`POST /dm/messages/{id}/read`)

## Security Best Practices

1. **Never log plaintext** - Only log message IDs and metadata
2. **Secure key storage** - Use platform keychain/secure enclave
3. **Key rotation** - Rotate signed prekeys every 60 days
4. **OTPK pool management** - Keep pool above 20 prekeys
5. **Safety number verification** - Show warnings on identity key changes
6. **Device revocation** - Allow users to revoke compromised devices
7. **Forward secrecy** - Double Ratchet provides this automatically
8. **Post-compromise security** - New sessions use fresh keys

## Testing

### Unit Tests
- Key generation
- X3DH key agreement
- Double Ratchet encrypt/decrypt
- Safety number computation

### Integration Tests
- End-to-end message flow
- Multi-device scenarios
- Key rotation
- Device revocation

### Security Tests
- Verify server never sees plaintext
- Test forward secrecy (old messages can't be decrypted after key change)
- Test post-compromise security
- Verify OTPK claiming prevents reuse

## Troubleshooting

### "No active device found"
- User needs to register a device first
- Call `uploadKeyBundle()` on app launch

### "Prekey not found or already claimed"
- OTPK pool exhausted
- Refetch key bundle and try again
- Peer should upload more prekeys

### "Token expired"
- SSE connection will disconnect
- Client should reconnect with fresh token

### "Rate limit exceeded"
- User sending too many messages
- Implement client-side rate limiting UI

## API Reference

See the main API documentation at `/docs` for complete endpoint specifications.

## Additional Resources

- [Signal Protocol Specification](https://signal.org/docs/)
- [Double Ratchet Algorithm](https://signal.org/docs/specifications/doubleratchet/)
- [X3DH Key Agreement](https://signal.org/docs/specifications/x3dh/)
- [libsignal-protocol-typescript](https://github.com/privacyresearchgroup/libsignal-protocol-typescript)

