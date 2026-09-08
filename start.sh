#!/bin/sh
set -e

cd /app
export PYTHONPATH=/app

echo "[ushnotice] Running database migrations..."
alembic upgrade head
echo "[ushnotice] Migrations complete. Starting server..."

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
