#!/usr/bin/env bash
set -euo pipefail

# Environment variables loaded

# Prefer TELEGRAM_API_ID/TELEGRAM_API_HASH. Fallback to API_ID/API_HASH from .env
TELEGRAM_API_ID="${TELEGRAM_API_ID:-${API_ID:-}}"
TELEGRAM_API_HASH="${TELEGRAM_API_HASH:-${API_HASH:-}}"

if [[ -z "${TELEGRAM_API_ID}" || -z "${TELEGRAM_API_HASH}" ]]; then
  echo "ERROR: TELEGRAM_API_ID/API_ID and TELEGRAM_API_HASH/API_HASH must be set"
  exit 1
fi

# Ports
PORT="${PORT:-8081}"
HEALTH_PORT="${HEALTH_PORT:-10000}"
# If Render assigned PORT equals HEALTH_PORT, move health to a different port to avoid conflict
if [ "$HEALTH_PORT" = "$PORT" ]; then
  NEW_HEALTH=$((PORT + 1))
  echo "HEALTH_PORT ($HEALTH_PORT) equals PORT ($PORT). Using $NEW_HEALTH for health server instead."
  HEALTH_PORT="$NEW_HEALTH"
fi

# Start Telegram Bot API server (listens on Render PORT)
telegram-bot-api \
  --api-id="${TELEGRAM_API_ID}" \
  --api-hash="${TELEGRAM_API_HASH}" \
  --http-port="${PORT}" \
  --dir=/var/lib/telegram-bot-api \
  --temp-dir=/tmp/telegram-bot-api &

# Wait for the Bot API server to become ready (max ~60s)
echo "Waiting for Bot API server on 127.0.0.1:${PORT}..."
for i in $(seq 1 60); do
  if [[ -n "${BOT_TOKEN:-}" ]]; then
    code=$(curl -sS -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT}/bot${BOT_TOKEN}/getMe" || true)
    if echo "$code" | grep -Eq '^(200|401)$'; then
      echo "Bot API is reachable (HTTP $code)."
      break
    fi
  else
    # No token provided: consider any HTTP response from root as success
    if curl -sS -o /dev/null "http://127.0.0.1:${PORT}/"; then
      echo "Bot API root reachable."
      break
    fi
  fi
  sleep 1
done

# Point our Python bot to the local Bot API server inside the container, unless already set
export BOT_API_BASE_URL="${BOT_API_BASE_URL:-http://127.0.0.1:${PORT}/bot}"
export BOT_API_BASE_FILE_URL="${BOT_API_BASE_FILE_URL:-http://127.0.0.1:${PORT}/file/bot}"

# Launch the Python bot (health server will bind to HEALTH_PORT)
exec python3 main.py
