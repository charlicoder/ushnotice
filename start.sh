#!/bin/sh
# =============================================================================
# ushnotice — Container startup script (PRODUCTION)
# Runs Alembic migrations then starts the ASGI server.
# Workers are read from the WORKERS env var (default: 2).
# =============================================================================
set -e

cd /app
export PYTHONPATH=/app

echo "[ushnotice] Running database migrations..."
alembic upgrade head
echo "[ushnotice] Migrations complete. Starting server..."

WORKERS="${WORKERS:-2}"
echo "[ushnotice] Starting uvicorn with ${WORKERS} worker(s)..."

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "${WORKERS}" \
    --loop uvloop \
    --http httptools \
    --proxy-headers \
    --forwarded-allow-ips "*"
