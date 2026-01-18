# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=on \
    POETRY_VIRTUALENVS_CREATE=0 \
    PORT=8000

WORKDIR /app

# Install build dependencies for packages like bcrypt
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first to leverage Docker layer caching
COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential

# Copy the rest of the application code
COPY . .

# Create a non-root user for runtime and ensure the start script is executable
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /home/app --create-home --shell /usr/sbin/nologin app \
    && chmod +x /app/start.sh

USER app

EXPOSE 8000

CMD ["./start.sh"]
