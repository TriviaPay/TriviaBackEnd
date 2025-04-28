# Extended Profile and Country Codes APIs

## Overview

This implementation adds multiple API endpoints to handle user profile data and country code information:

1. Extended profile update API (`/profile/extended-update`) with one-time username change restriction
2. Complete profile information API (`/profile/complete`) to retrieve all user profile fields
3. Country codes API (`/profile/country-codes`) to retrieve country calling codes with animated waving flags

## Database Changes

The following database changes were made:

1. Added `username_updated` column to the `users` table (Boolean, default: false)
2. Added `gender` column to the `users` table (String, nullable)
3. Created a new `country_codes` table for storing country calling codes and flags
4. Updated the User model to include these new fields

## API Endpoints

### 1. Extended Profile Update

**Endpoint:** `POST /profile/extended-update`

**Request Body:**
```json
{
  "username": "optional_new_username",
  "first_name": "John",
  "last_name": "Doe",
  "mobile": "123-456-7890",
  "country_code": "+1",
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
    "country_code": "+1",
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

### 2. Complete Profile Information

**Endpoint:** `GET /profile/complete`

**Response:**
```json
{
  "status": "success",
  "data": {
    "account_id": 1234567890,
    "email": "user@example.com",
    "mobile": "123-456-7890",
    "country_code": "+1",
    "first_name": "John",
    "last_name": "Doe",
    "middle_name": "A",
    "username": "john_doe",
    "gender": "Male",
    "date_of_birth": "1990-01-01",
    "sign_up_date": "2023-01-01T12:00:00",
    "address": {
      "street_1": "123 Main St",
      "street_2": "Apt 4B",
      "suite_or_apt_number": "4B",
      "city": "New York",
      "state": "NY",
      "zip": "10001",
      "country": "USA"
    },
    "profile_pic_url": "https://example.com/profile.jpg",
    "username_updated": true,
    "referral_code": "12345",
    "is_referred": false
  }
}
```

### 3. Country Codes with Flags

**Endpoint:** `GET /profile/country-codes`

**Response:**
```json
{
  "status": "success",
  "data": [
    {
      "code": "+1",
      "country_name": "United States",
      "flag_url": "https://flagsapi.com/US/animated/64.gif",
      "country_iso": "US"
    },
    {
      "code": "+44",
      "country_name": "United Kingdom",
      "flag_url": "https://flagsapi.com/GB/animated/64.gif", 
      "country_iso": "GB"
    },
    ...
  ]
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

## Country Codes Feature

- The system includes a database of country calling codes with their animated waving flag images
- These can be used in the mobile number input field to allow users to select their country code
- Each country code includes:
  - The calling code (e.g., "+1", "+44")
  - The country name (e.g., "United States", "United Kingdom")
  - A URL to the country's animated waving flag GIF image
  - The country's ISO code (e.g., "US", "GB")
- The animated flags add visual appeal and improve user engagement when selecting country codes

## Migration Scripts

- `migrations/add_username_updated_field.py`: Adds the `username_updated` column to the `users` table
- `migrations/add_gender_column.py`: Adds the `gender` column to the `users` table
- `migrations/populate_country_codes.py`: Creates and populates the `country_codes` table with common country codes and their flag URLs

## Testing

To test the APIs:

1. Make sure the FastAPI server is running
2. Run the included test scripts or use the Swagger UI at `/docs`
3. For testing the profile update endpoint with username restrictions:
   ```bash
   ./test_direct_profile_update.sh
   ``` 