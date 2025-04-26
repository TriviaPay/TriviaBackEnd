# Extended Profile Update Implementation

## Overview

This implementation adds a new API endpoint to update extended user profile fields and implements a one-time username update restriction. The following features were added:

1. A new API endpoint: `/profile/extended-update` to update additional user profile fields
2. Support for updating:
   - Mobile number
   - Gender
   - Address fields (street, city, state, zip)
   - First name
   - Last name
3. One-time username update restriction - users can only change their username once

## Database Changes

The following database changes were made:

1. Added `username_updated` column to the `users` table (Boolean, default: false)
2. Added `gender` column to the `users` table (String, nullable)
3. Updated the User model to include these new fields

## API Endpoint

### Extended Profile Update

**Endpoint:** `POST /profile/extended-update`

**Request Body:**
```json
{
  "username": "optional_new_username",
  "first_name": "John",
  "last_name": "Doe",
  "mobile": "123-456-7890",
  "gender": "Male",
  "street_1": "123 Main St",
  "street_2": "Apt 4B",
  "suite_or_apt_number": "4B",
  "city": "New York",
  "state": "NY",
  "zip": "10001",
  "country": "USA"
}
```

All fields are optional. Any provided field will be updated, and any omitted field will remain unchanged.

**Response:**
```json
{
  "status": "success",
  "message": "Profile updated successfully",
  "data": {
    "username": "john_doe",
    "first_name": "John",
    "last_name": "Doe",
    "mobile": "123-456-7890",
    "gender": "Male",
    "address": {
      "street_1": "123 Main St",
      "street_2": "Apt 4B",
      "suite_or_apt_number": "4B",
      "city": "New York",
      "state": "NY",
      "zip": "10001",
      "country": "USA"
    },
    "username_updated": true
  }
}
```

## Username Update Restriction

- Users can only update their username once
- When a username is changed, the `username_updated` flag is set to `true`
- Subsequent attempts to change the username will return an error
- Other profile fields can still be updated even after the username has been changed

**Error Response for Multiple Username Updates:**
```json
{
  "status": "error",
  "message": "You have already used your free username update. Username cannot be changed again.",
  "code": "USERNAME_ALREADY_UPDATED"
}
```

## Profile Picture Update

- When a username is updated, the profile picture URL is automatically updated to use the first letter of the new username
- This leverages the existing `get_letter_profile_pic` function

## Testing

To test the API:

1. Make sure the FastAPI server is running
2. Update the `ACCESS_TOKEN` variable in `test_direct_profile_update.sh`
3. Run the test script:
   ```
   ./test_direct_profile_update.sh
   ```

The script tests:
1. Initial profile update with username change
2. Second attempt to change username (should fail)
3. Update of other fields without changing username (should succeed)

## Migration Scripts

- `migrations/add_username_updated_field.py`: Adds the `username_updated` column to the `users` table
- `migrations/add_gender_column.py`: Adds the `gender` column to the `users` table 