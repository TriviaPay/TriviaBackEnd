#!/bin/bash

# Test script for extended profile update

# Replace with an actual access token for testing
ACCESS_TOKEN="your_real_access_token_here"

# Generate a unique test username
TIMESTAMP=$(date +%s)
TEST_USERNAME="testuser_${TIMESTAMP}"

echo "=== Testing Extended Profile Update API ==="
echo "1. First update with username: ${TEST_USERNAME}"

# First update - should succeed
curl -X POST http://localhost:8000/profile/extended-update \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -d "{
    \"username\": \"${TEST_USERNAME}\",
    \"first_name\": \"Test\",
    \"last_name\": \"User\",
    \"mobile\": \"123-456-7890\",
    \"gender\": \"Male\",
    \"street_1\": \"123 Test St\",
    \"city\": \"Test City\",
    \"state\": \"Test State\",
    \"zip\": \"12345\",
    \"country\": \"USA\"
  }" | python -m json.tool

echo ""
echo "2. Second update trying to change username (should fail with username_updated error)"

# Second update with different username - should fail due to restriction
NEW_USERNAME="newusername_${TIMESTAMP}"
curl -X POST http://localhost:8000/profile/extended-update \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -d "{
    \"username\": \"${NEW_USERNAME}\",
    \"first_name\": \"Updated\",
    \"last_name\": \"User\"
  }" | python -m json.tool

echo ""
echo "3. Third update without changing username (should succeed)"

# Third update without username - should succeed
curl -X POST http://localhost:8000/profile/extended-update \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -d "{
    \"first_name\": \"Final\",
    \"last_name\": \"Update\",
    \"gender\": \"Other\"
  }" | python -m json.tool

echo ""
echo "=== Test Completed ===" 