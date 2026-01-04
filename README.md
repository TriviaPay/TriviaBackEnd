# TriviaPay Backend

Backend API for the TriviaPay application.

## Deployment

This application is deployed on [Vercel](https://vercel.com) using the configuration in `vercel.json`.

## Local Development

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run the development server:
   ```bash
   uvicorn main:app --reload
   ```

3. Access the API documentation at http://localhost:8000/docs

## Docker

### Build & Run the API locally

```bash
# Build the image (only needed the first time or when dependencies change)
docker build -t triviapay-api .

# Run the container (requires a populated .env file)
docker run --env-file .env -p 8000:8000 triviapay-api
```

### Using Docker Compose

The repository includes a `docker-compose.yml` that provisions the API, PostgreSQL, and Redis with sensible defaults.

```bash
# Start the full stack (API + Postgres + Redis)
docker compose up --build

# Tear everything down when finished
docker compose down
```

The compose file automatically wires `DATABASE_URL` and `REDIS_URL` to the local Postgres/Redis containers unless they are already defined in your shell. Override them in your `.env` if you need to connect to external services.

## Configuration

The application uses environment variables for configuration, which can be set in a `.env` file for local development.

## Automatic Deployment

The application is automatically deployed to Vercel when changes are pushed to the main branch using GitHub Actions.

## Authentication (Descope)

### Current Flow
1. **OTP Authentication**: Users verify email via OTP using Descope
2. **Profile Binding**: After OTP success, users can bind additional profile information
3. **Session Management**: All requests use Descope session JWTs for authentication

### Endpoints

#### `POST /bind-password`
Binds user profile information after successful OTP authentication.

**Headers:**
- `Authorization: Bearer <descope_session_jwt>`
- `Content-Type: application/json`

**Body:**
```json
{
  "email": "user@example.com",
  "password": "StrongP4ssw0rd",
  "username": "UserName",
  "country": "United States",
  "date_of_birth": "1995-06-30"
}
```

**Note:** Currently stores profile data locally. Password authentication via Descope will be implemented in a future update.

#### `GET /username-available?username=<username>`
Check if a username is available.

**Response:**
```json
{
  "available": true
}
```

### Session Refresh
After binding profile data, call `descope.session.refresh()` on the frontend to pick up updated user information.

### Future Authentication
Once password binding is fully implemented, users will be able to authenticate with:
```javascript
descope.password.signIn(emailOrUsername, password)
``` 
