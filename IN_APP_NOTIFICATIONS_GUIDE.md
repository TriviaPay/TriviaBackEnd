# In-App Notifications Implementation Guide

## Overview

The backend sends notifications with a `show_as_in_app` flag in the data payload. When this flag is `true`, the frontend should display an in-app notification instead of relying on the system push notification (which OneSignal suppresses when the app is in foreground).

## How the 30-Second Rule Works

**Yes, the 30-second rule is still applying!**

### Backend Logic:
1. **Active User (within 30 seconds)**: 
   - If user's `OneSignalPlayer.last_active` is within the last 30 seconds
   - Backend sends notification with `is_in_app_notification=True`
   - Data payload includes: `{"show_as_in_app": true, ...}`

2. **Inactive User (more than 30 seconds)**:
   - If user's `OneSignalPlayer.last_active` is older than 30 seconds
   - Backend sends notification with `is_in_app_notification=False`
   - Data payload does NOT include `show_as_in_app` flag
   - System push notification is sent (works when app is in background)

### Activity Tracking:
- `last_active` is updated when user registers/updates their OneSignal player ID
- The frontend should call the `/onesignal/register` endpoint periodically (or on app foreground) to update `last_active`

## Frontend Implementation

### Step 1: Update OneSignal Player Activity

Call the register endpoint when the app comes to foreground or periodically:

```typescript
// When app comes to foreground
import * as Notifications from 'expo-notifications';

// Update activity when app is active
async function updatePlayerActivity() {
  const playerId = await getOneSignalPlayerId(); // Get from OneSignal SDK
  if (playerId) {
    await fetch(`${API_URL}/onesignal/register`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${accessToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        player_id: playerId,
        platform: Platform.OS === 'ios' ? 'ios' : 'android',
      }),
    });
  }
}

// Call on app foreground
AppState.addEventListener('change', (nextAppState) => {
  if (nextAppState === 'active') {
    updatePlayerActivity();
  }
});
```

### Step 2: Listen for Notification Received Events

OneSignal SDK provides notification received events. You need to handle both foreground and background cases:

```typescript
import OneSignal from 'react-native-onesignal';

// Set up notification handlers
OneSignal.setNotificationWillShowInForegroundHandler((notificationReceivedEvent) => {
  const notification = notificationReceivedEvent.getNotification();
  const data = notification.additionalData || {};
  
  // Check if this should be shown as in-app notification
  if (data.show_as_in_app === true) {
    // Cancel the system notification
    notificationReceivedEvent.complete();
    
    // Show custom in-app notification
    showInAppNotification(notification);
  } else {
    // Let OneSignal show the system notification
    notificationReceivedEvent.complete(notification);
  }
});

// Handle notification received in background (when app opens from notification)
OneSignal.setNotificationOpenedHandler((result) => {
  const data = result.notification.additionalData || {};
  handleNotificationNavigation(data);
});
```

### Step 3: Create In-App Notification Component

Create a custom in-app notification UI component:

```typescript
import React, { useState, useEffect } from 'react';
import { View, Text, TouchableOpacity, Animated, StyleSheet } from 'react-native';

interface InAppNotification {
  id: string;
  heading: string;
  content: string;
  data: any;
  timestamp: number;
}

const InAppNotificationComponent = () => {
  const [notifications, setNotifications] = useState<InAppNotification[]>([]);
  const slideAnim = new Animated.Value(-100);

  const showInAppNotification = (notification: any) => {
    const inAppNotification: InAppNotification = {
      id: Date.now().toString(),
      heading: notification.title || notification.headings?.en || 'Notification',
      content: notification.body || notification.contents?.en || '',
      data: notification.additionalData || {},
      timestamp: Date.now(),
    };

    setNotifications((prev) => [...prev, inAppNotification]);

    // Animate in
    Animated.spring(slideAnim, {
      toValue: 0,
      useNativeDriver: true,
    }).start();

    // Auto-dismiss after 5 seconds
    setTimeout(() => {
      dismissNotification(inAppNotification.id);
    }, 5000);
  };

  const dismissNotification = (id: string) => {
    Animated.timing(slideAnim, {
      toValue: -100,
      duration: 300,
      useNativeDriver: true,
    }).start(() => {
      setNotifications((prev) => prev.filter((n) => n.id !== id));
    });
  };

  const handleNotificationPress = (notification: InAppNotification) => {
    dismissNotification(notification.id);
    handleNotificationNavigation(notification.data);
  };

  if (notifications.length === 0) return null;

  const currentNotification = notifications[0];

  return (
    <Animated.View
      style={[
        styles.container,
        {
          transform: [{ translateY: slideAnim }],
        },
      ]}
    >
      <TouchableOpacity
        style={styles.notification}
        onPress={() => handleNotificationPress(currentNotification)}
        activeOpacity={0.8}
      >
        <Text style={styles.heading}>{currentNotification.heading}</Text>
        <Text style={styles.content} numberOfLines={2}>
          {currentNotification.content}
        </Text>
      </TouchableOpacity>
    </Animated.View>
  );
};

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    top: 50,
    left: 10,
    right: 10,
    zIndex: 9999,
  },
  notification: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 16,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.25,
    shadowRadius: 8,
    elevation: 5,
    borderLeftWidth: 4,
    borderLeftColor: '#007AFF',
  },
  heading: {
    fontSize: 16,
    fontWeight: 'bold',
    marginBottom: 4,
    color: '#000',
  },
  content: {
    fontSize: 14,
    color: '#666',
  },
});

export default InAppNotificationComponent;
```

### Step 4: Handle Notification Navigation

Navigate to the appropriate screen based on notification type:

```typescript
function handleNotificationNavigation(data: any) {
  const { type, conversation_id, sender_id, message_id, draw_date } = data;

  switch (type) {
    case 'private_message':
    case 'chat_request':
      // Navigate to private chat
      navigation.navigate('PrivateChat', { conversationId: conversation_id });
      break;

    case 'global_chat':
      // Navigate to global chat
      navigation.navigate('GlobalChat');
      break;

    case 'trivia_live_chat':
      // Navigate to trivia live chat
      navigation.navigate('TriviaLiveChat', { drawDate: draw_date });
      break;

    case 'trivia_reminder':
      // Navigate to trivia questions
      navigation.navigate('Trivia');
      break;

    default:
      console.log('Unknown notification type:', type);
  }
}
```

### Step 5: Integrate into App Root

Add the in-app notification component to your app root:

```typescript
import InAppNotificationComponent from './components/InAppNotificationComponent';

function App() {
  return (
    <NavigationContainer>
      {/* Your app screens */}
      <Stack.Navigator>
        {/* ... */}
      </Stack.Navigator>
      
      {/* In-app notification overlay */}
      <InAppNotificationComponent />
    </NavigationContainer>
  );
}
```

## Notification Data Payload Structure

### Private Chat
```json
{
  "type": "private_message",
  "conversation_id": 123,
  "sender_id": 456,
  "show_as_in_app": true
}
```

### Global Chat
```json
{
  "type": "global_chat",
  "message_id": 789,
  "sender_id": 456,
  "sender_username": "John",
  "message": "Hello world",
  "created_at": "2025-11-29T12:00:00Z",
  "show_as_in_app": true
}
```

### Trivia Live Chat
```json
{
  "type": "trivia_live_chat",
  "message_id": 101,
  "sender_id": 456,
  "sender_username": "John",
  "message": "Great question!",
  "draw_date": "2025-11-29",
  "created_at": "2025-11-29T12:00:00Z",
  "show_as_in_app": true
}
```

### Trivia Reminder
```json
{
  "type": "trivia_reminder",
  "draw_date": "2025-11-29"
}
```
Note: Trivia reminders are always system push (not in-app) as they're sent to inactive users.

## Testing

### Test In-App Notifications:
1. Open the app (foreground)
2. Have another user send a message
3. If you were active within 30 seconds, you should see an in-app notification
4. If you were inactive for more than 30 seconds, you should see a system push notification

### Test Activity Tracking:
1. Call `/onesignal/register` endpoint
2. Check backend logs to see `last_active` is updated
3. Send a message within 30 seconds → should be in-app
4. Wait 31+ seconds → should be system push

## Debugging

### Backend Logs to Check:
- `"Preparing push notification | is_active=true"` → In-app notification
- `"Preparing push notification | is_active=false"` → System push
- `"✅ OneSignal in-app notification sent successfully"` → Confirms in-app flag was sent
- `"show_as_in_app=True"` in payload logs

### Frontend Debugging:
- Log notification received events
- Check if `data.show_as_in_app === true`
- Verify in-app notification component is rendering
- Check navigation is working correctly

## Important Notes

1. **OneSignal suppresses notifications in foreground**: By default, OneSignal doesn't show notifications when the app is in foreground. The `show_as_in_app` flag tells the frontend to handle it manually.

2. **Activity tracking is crucial**: The 30-second rule depends on `last_active` being updated. Make sure to call the register endpoint when the app comes to foreground.

3. **Both notification types are sent**: The backend always sends a notification via OneSignal. The difference is:
   - **In-app**: `show_as_in_app=true` → Frontend shows custom UI
   - **System push**: `show_as_in_app` not set → OneSignal shows system notification (when app is in background)

4. **Notification queuing**: If multiple notifications arrive, you may want to queue them and show one at a time.

## Example: Complete Integration

```typescript
// App.tsx
import { useEffect } from 'react';
import { AppState } from 'react-native';
import OneSignal from 'react-native-onesignal';
import InAppNotificationComponent from './components/InAppNotificationComponent';

function App() {
  useEffect(() => {
    // Update activity on app foreground
    const subscription = AppState.addEventListener('change', (nextAppState) => {
      if (nextAppState === 'active') {
        updatePlayerActivity();
      }
    });

    // Set up OneSignal handlers
    OneSignal.setNotificationWillShowInForegroundHandler((event) => {
      const notification = event.getNotification();
      const data = notification.additionalData || {};
      
      if (data.show_as_in_app === true) {
        event.complete(); // Cancel system notification
        showInAppNotification(notification); // Show custom UI
      } else {
        event.complete(notification); // Let OneSignal handle it
      }
    });

    OneSignal.setNotificationOpenedHandler((result) => {
      handleNotificationNavigation(result.notification.additionalData || {});
    });

    return () => subscription.remove();
  }, []);

  return (
    <NavigationContainer>
      {/* Your app */}
      <InAppNotificationComponent />
    </NavigationContainer>
  );
}
```

This implementation ensures that:
- Active users (within 30 seconds) see in-app notifications
- Inactive users (more than 30 seconds) see system push notifications
- All notifications are properly handled and navigated to the correct screen

