#!/bin/bash

# Manual test script for chat mute and push notification endpoints
# Usage: ./test_chat_endpoints_manual.sh <USER1_TOKEN> <USER2_TOKEN> <USER2_ID>

BASE_URL="http://127.0.0.1:8000"

if [ $# -lt 3 ]; then
    echo "Usage: $0 <USER1_TOKEN> <USER2_TOKEN> <USER2_ID>"
    echo "Example: $0 'eyJhbGc...' 'eyJhbGc...' 1234567890"
    exit 1
fi

USER1_TOKEN=$1
USER2_TOKEN=$2
USER2_ID=$3

echo "============================================================"
echo "CHAT MUTE AND PUSH NOTIFICATION ENDPOINT TESTS"
echo "============================================================"
echo ""

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test 1: Get mute preferences
echo -e "${YELLOW}Test 1: GET /chat-mute/preferences${NC}"
response=$(curl -s -w "\n%{http_code}" -X GET \
  "${BASE_URL}/chat-mute/preferences" \
  -H "Authorization: Bearer ${USER1_TOKEN}" \
  -H "accept: application/json")
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')
echo "Status: $http_code"
echo "Response: $body" | jq '.' 2>/dev/null || echo "$body"
echo ""

# Test 2: Mute global chat
echo -e "${YELLOW}Test 2: POST /chat-mute/global (mute)${NC}"
response=$(curl -s -w "\n%{http_code}" -X POST \
  "${BASE_URL}/chat-mute/global" \
  -H "Authorization: Bearer ${USER1_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"muted": true}')
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')
echo "Status: $http_code"
echo "Response: $body" | jq '.' 2>/dev/null || echo "$body"
echo ""

# Test 3: Verify global chat is muted
echo -e "${YELLOW}Test 3: GET /chat-mute/preferences (verify mute)${NC}"
response=$(curl -s -w "\n%{http_code}" -X GET \
  "${BASE_URL}/chat-mute/preferences" \
  -H "Authorization: Bearer ${USER1_TOKEN}" \
  -H "accept: application/json")
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')
echo "Status: $http_code"
echo "Response: $body" | jq '.' 2>/dev/null || echo "$body"
echo ""

# Test 4: Mute trivia live chat
echo -e "${YELLOW}Test 4: POST /chat-mute/trivia-live (mute)${NC}"
response=$(curl -s -w "\n%{http_code}" -X POST \
  "${BASE_URL}/chat-mute/trivia-live" \
  -H "Authorization: Bearer ${USER1_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"muted": true}')
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')
echo "Status: $http_code"
echo "Response: $body" | jq '.' 2>/dev/null || echo "$body"
echo ""

# Test 5: Unmute global chat
echo -e "${YELLOW}Test 5: POST /chat-mute/global (unmute)${NC}"
response=$(curl -s -w "\n%{http_code}" -X POST \
  "${BASE_URL}/chat-mute/global" \
  -H "Authorization: Bearer ${USER1_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"muted": false}')
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')
echo "Status: $http_code"
echo "Response: $body" | jq '.' 2>/dev/null || echo "$body"
echo ""

# Test 6: Mute user for private chat
echo -e "${YELLOW}Test 6: POST /chat-mute/private/${USER2_ID} (mute user)${NC}"
response=$(curl -s -w "\n%{http_code}" -X POST \
  "${BASE_URL}/chat-mute/private/${USER2_ID}" \
  -H "Authorization: Bearer ${USER1_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"muted": true}')
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')
echo "Status: $http_code"
echo "Response: $body" | jq '.' 2>/dev/null || echo "$body"
echo ""

# Test 7: List muted users
echo -e "${YELLOW}Test 7: GET /chat-mute/private (list muted users)${NC}"
response=$(curl -s -w "\n%{http_code}" -X GET \
  "${BASE_URL}/chat-mute/private" \
  -H "Authorization: Bearer ${USER1_TOKEN}" \
  -H "accept: application/json")
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')
echo "Status: $http_code"
echo "Response: $body" | jq '.' 2>/dev/null || echo "$body"
echo ""

# Test 8: Send global chat message (triggers push notification)
echo -e "${YELLOW}Test 8: POST /global-chat/send${NC}"
TIMESTAMP=$(date +%s)
response=$(curl -s -w "\n%{http_code}" -X POST \
  "${BASE_URL}/global-chat/send" \
  -H "Authorization: Bearer ${USER1_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"Test global chat message with push notification\", \"client_message_id\": \"test_global_${TIMESTAMP}\"}")
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')
echo "Status: $http_code"
echo "Response: $body" | jq '.' 2>/dev/null || echo "$body"
echo "Note: Push notifications are sent in background to all users (except sender)"
echo "      who are not muted and not active (30-second threshold)"
echo ""

# Test 9: Get global chat messages (verify avatar, frame, badge)
echo -e "${YELLOW}Test 9: GET /global-chat/messages${NC}"
response=$(curl -s -w "\n%{http_code}" -X GET \
  "${BASE_URL}/global-chat/messages?limit=10" \
  -H "Authorization: Bearer ${USER1_TOKEN}" \
  -H "accept: application/json")
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')
echo "Status: $http_code"
echo "Response: $body" | jq '.' 2>/dev/null || echo "$body"
echo ""

# Test 10: Check trivia live chat status
echo -e "${YELLOW}Test 10: GET /trivia-live-chat/status${NC}"
response=$(curl -s -w "\n%{http_code}" -X GET \
  "${BASE_URL}/trivia-live-chat/status" \
  -H "Authorization: Bearer ${USER1_TOKEN}" \
  -H "accept: application/json")
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')
echo "Status: $http_code"
echo "Response: $body" | jq '.' 2>/dev/null || echo "$body"
echo ""

# Test 11: Send trivia live chat message (if active)
echo -e "${YELLOW}Test 11: POST /trivia-live-chat/send${NC}"
TIMESTAMP=$(date +%s)
response=$(curl -s -w "\n%{http_code}" -X POST \
  "${BASE_URL}/trivia-live-chat/send" \
  -H "Authorization: Bearer ${USER1_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"Test trivia live chat message with push notification\", \"client_message_id\": \"test_trivia_${TIMESTAMP}\"}")
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')
echo "Status: $http_code"
echo "Response: $body" | jq '.' 2>/dev/null || echo "$body"
if [ "$http_code" = "200" ]; then
    echo "Note: Push notifications are sent in background to all users (except sender)"
    echo "      who are not muted and not active (30-second threshold)"
fi
echo ""

# Test 12: Get trivia live chat messages (verify avatar, frame, badge)
echo -e "${YELLOW}Test 12: GET /trivia-live-chat/messages${NC}"
response=$(curl -s -w "\n%{http_code}" -X GET \
  "${BASE_URL}/trivia-live-chat/messages?limit=10" \
  -H "Authorization: Bearer ${USER1_TOKEN}" \
  -H "accept: application/json")
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')
echo "Status: $http_code"
echo "Response: $body" | jq '.' 2>/dev/null || echo "$body"
echo ""

# Test 13: Send private message (triggers push with mute check)
echo -e "${YELLOW}Test 13: POST /private-chat/send${NC}"
TIMESTAMP=$(date +%s)
response=$(curl -s -w "\n%{http_code}" -X POST \
  "${BASE_URL}/private-chat/send" \
  -H "Authorization: Bearer ${USER1_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"recipient_id\": ${USER2_ID}, \"message\": \"Test private message with mute checking\", \"client_message_id\": \"test_private_${TIMESTAMP}\"}")
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')
echo "Status: $http_code"
echo "Response: $body" | jq '.' 2>/dev/null || echo "$body"
if [ "$http_code" = "200" ]; then
    echo "Note: Push notification will be sent if user2 is not muted, not active, and has OneSignal player"
fi
echo ""

# Test 14: List private conversations (verify avatar, frame, badge)
echo -e "${YELLOW}Test 14: GET /private-chat/conversations${NC}"
response=$(curl -s -w "\n%{http_code}" -X GET \
  "${BASE_URL}/private-chat/conversations" \
  -H "Authorization: Bearer ${USER1_TOKEN}" \
  -H "accept: application/json")
http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | sed '$d')
echo "Status: $http_code"
echo "Response: $body" | jq '.' 2>/dev/null || echo "$body"
echo ""

echo "============================================================"
echo "ALL TESTS COMPLETED"
echo "============================================================"
echo ""
echo "Note: Push notifications are sent asynchronously in background tasks."
echo "      Check your OneSignal dashboard to verify push notifications were sent."
echo "      Check server logs for push notification delivery status."

