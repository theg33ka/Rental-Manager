#!/usr/bin/env sh
set -eu

PORT_VALUE="${PORT:-10000}"
export RENTAL_MANAGER_SETTINGS_ENCRYPTION_KEY_FILE="${RENTAL_MANAGER_SETTINGS_ENCRYPTION_KEY_FILE:-/data/rental-manager-settings.key}"
echo "[BOOT] rental-manager starting port=${PORT_VALUE} db_configured=$([ -n "${RENTAL_MANAGER_DATABASE_URL:-}${DATABASE_URL:-}" ] && echo true || echo false) telegram_env_token_configured=$([ -n "${TELEGRAM_BOT_TOKEN:-}" ] && echo true || echo false) deepseek_env_key_configured=$([ -n "${DEEPSEEK_API_KEY:-}" ] && echo true || echo false)"

echo "[BOOT] applying database migrations"
alembic upgrade head

echo "[BOOT] starting uvicorn with direct DeepSeek API integration"
exec uvicorn rental_manager.main:app --host 0.0.0.0 --port "$PORT_VALUE"
