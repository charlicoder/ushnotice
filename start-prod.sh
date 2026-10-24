#!/usr/bin/env bash
# =============================================================================
# start-prod.sh — Start ushnotice in PRODUCTION mode
#
# - Uses Dockerfile (multi-stage production build with hatchling)
# - Uses docker-compose.prod.yml (no volume mount)
# - Workers set via WORKERS env var in .env (default: 2)
#
# Usage:
#   ./start-prod.sh          → build and start in background
#   ./start-prod.sh --build  → force full rebuild
# =============================================================================

set -euo pipefail

COMPOSE_FILE="docker-compose.prod.yml"
SERVICE_NAME="ushnotice"
PORT="8004"

# Check .env exists
if [ ! -f ".env" ]; then
  echo "❌  .env file not found."
  echo "   Copy and configure: cp .env.example .env"
  exit 1
fi

# Warn if WORKERS is not set or is 1 (low for production)
WORKERS_VAL=$(grep -E "^WORKERS=" .env | cut -d '=' -f2 | tr -d '[:space:]' || true)
if [ -z "$WORKERS_VAL" ] || [ "$WORKERS_VAL" = "1" ]; then
  echo "⚠️  WARNING: WORKERS not set or is 1 in .env"
  echo "   For production, set WORKERS=2 (or more) in .env"
  echo ""
fi

echo "🚀  Starting ${SERVICE_NAME} in PRODUCTION mode..."
echo "    Compose file : $COMPOSE_FILE"
echo "    Port         : ${PORT} (host) → 8000 (container)"
echo "    Workers      : ${WORKERS_VAL:-2 (default)}"
echo ""

docker compose -f "$COMPOSE_FILE" up --build -d "$@"

echo ""
echo "✅  ${SERVICE_NAME} is running in the background on port ${PORT}."
echo "    View logs:  docker compose -f $COMPOSE_FILE logs -f"
echo "    Stop:       docker compose -f $COMPOSE_FILE down"
