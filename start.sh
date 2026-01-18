#!/bin/sh
# Start script for deployment

set -e

export PYTHONPATH=.

PORT="${PORT:-8000}"
ENVIRONMENT="${ENVIRONMENT:-development}"
UVICORN_KEEP_ALIVE="${UVICORN_KEEP_ALIVE:-300}"
UVICORN_BACKLOG="${UVICORN_BACKLOG:-2048}"
UVICORN_LOG_LEVEL="${UVICORN_LOG_LEVEL:-info}"

if [ -z "${UVICORN_WORKERS:-}" ]; then
  if [ "${ENVIRONMENT}" = "development" ]; then
    UVICORN_WORKERS=1
  else
    UVICORN_WORKERS=2
  fi
fi

echo "Starting app on port ${PORT} with ${UVICORN_WORKERS} workers"

exec uvicorn main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers "${UVICORN_WORKERS}" \
  --timeout-keep-alive "${UVICORN_KEEP_ALIVE}" \
  --backlog "${UVICORN_BACKLOG}" \
  --log-level "${UVICORN_LOG_LEVEL}"
