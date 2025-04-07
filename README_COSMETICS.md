# Cosmetics System: Avatars and Profile Frames

This document outlines the implementation of the cosmetics system for TriviaApp, which includes customizable avatars and profile frames.

## Overview

The cosmetics system allows users to:
- View available avatars and frames in the store
- Purchase avatars and frames using gems or USD
- Select avatars and frames to customize their profile
- View their collection of owned cosmetics

Admins can:
- Add new avatars and frames
- Set prices in gems or USD
- Bulk import cosmetics from JSON files

## Database Schema

### Models

1. **Avatar**
   - Represents customizable profile pictures for users
   - Fields: id, name, description, image_url, price_gems, price_usd, is_premium, is_default, created_at

2. **Frame**
   - Represents decorative borders for profile pictures
   - Fields: id, name, description, image_url, price_gems, price_usd, is_premium, is_default, created_at

3. **UserAvatar**
   - Junction table to track which avatars a user owns
   - Fields: id, user_id, avatar_id, purchase_date

4. **UserFrame**
   - Junction table to track which frames a user owns
   - Fields: id, user_id, frame_id, purchase_date

5. **User (Updated)**
   - Added fields: selected_avatar_id, selected_frame_id

## API Endpoints

### Avatar Endpoints

- `GET /cosmetics/avatars` - Get all available avatars
- `GET /cosmetics/avatars/owned` - Get avatars owned by the current user
- `POST /cosmetics/avatars/buy/{avatar_id}` - Purchase an avatar
- `POST /cosmetics/avatars/select/{avatar_id}` - Select an avatar as current profile avatar
- `POST /cosmetics/admin/avatars` - Admin: Create a new avatar
- `POST /cosmetics/admin/avatars/import` - Admin: Bulk import avatars from JSON

### Frame Endpoints

- `GET /cosmetics/frames` - Get all available frames
- `GET /cosmetics/frames/owned` - Get frames owned by the current user
- `POST /cosmetics/frames/buy/{frame_id}` - Purchase a frame
- `POST /cosmetics/frames/select/{frame_id}` - Select a frame as current profile frame
- `POST /cosmetics/admin/frames` - Admin: Create a new frame
- `POST /cosmetics/admin/frames/import` - Admin: Bulk import frames from JSON

## Features

1. **Purchase Options**
   - Items can be purchased with either gems (in-game currency) or USD
   - Some items may be premium (USD only)
   - Default items are free for all users

2. **Sorting and Display**
   - Items are displayed with newest items first
   - The store shows all available items
   - The user's gallery shows only items they own or default items

3. **Admin Management**
   - Admins can add new items individually 
   - Admins can bulk import items from JSON files
   - Prices can be modified by admins

## Sample Data

A sample JSON file (`sample_cosmetics.json`) is provided with example avatars and frames that can be imported into the system.

## Frontend Implementation

Example React Native code is provided in `frontend_example.js` showing how to:
- Fetch and display avatars/frames
- Purchase cosmetic items
- Select and apply cosmetics to user profiles

## Migration

An Alembic migration script is included to add the necessary tables to the database:
- `migrations/versions/add_cosmetics_tables.py`

## How to Use

1. **Run the migration**
   ```
   alembic upgrade head
   ```

2. **Import sample data**
   ```
   curl -X POST http://localhost:8000/cosmetics/admin/avatars/import \
     -H "Authorization: Bearer YOUR_TOKEN" \
     -H "Content-Type: application/json" \
     -d @sample_cosmetics.json
   ```

3. **View available cosmetics**
   ```
   curl http://localhost:8000/cosmetics/avatars \
     -H "Authorization: Bearer YOUR_TOKEN"
   ```

4. **Purchase a cosmetic item**
   ```
   curl -X POST http://localhost:8000/cosmetics/avatars/buy/avatar_02?payment_method=gems \
     -H "Authorization: Bearer YOUR_TOKEN"
   ```

5. **Select a cosmetic item**
   ```
   curl -X POST http://localhost:8000/cosmetics/avatars/select/avatar_02 \
     -H "Authorization: Bearer YOUR_TOKEN"
   ``` 