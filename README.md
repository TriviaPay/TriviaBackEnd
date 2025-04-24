# TriviaPay Backend

Backend API for the TriviaPay application.

## Deployment

This application is deployed on [Render](https://render.com) using the configuration in `render.yaml`.

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

## Configuration

The application uses environment variables for configuration, which can be set in a `.env` file for local development.

## Automatic Deployment

The application is automatically deployed to Render when changes are pushed to the main branch using GitHub Actions. 