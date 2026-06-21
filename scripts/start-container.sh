#!/usr/bin/env sh
set -eu

PORT_VALUE="${PORT:-10000}"
echo "[BOOT] rental-manager starting port=${PORT_VALUE} db_configured=$([ -n "${RENTAL_MANAGER_DATABASE_URL:-}${DATABASE_URL:-}" ] && echo true || echo false) telegram_env_token_configured=$([ -n "${TELEGRAM_BOT_TOKEN:-}" ] && echo true || echo false)"

if [ -n "${HERMES_API_KEY:-}" ]; then
  export API_SERVER_ENABLED="${API_SERVER_ENABLED:-true}"
  export API_SERVER_KEY="${API_SERVER_KEY:-$HERMES_API_KEY}"
  export API_SERVER_HOST="${API_SERVER_HOST:-127.0.0.1}"
  export API_SERVER_PORT="${API_SERVER_PORT:-8642}"
  export API_SERVER_MODEL_NAME="${API_SERVER_MODEL_NAME:-hermes-agent}"
  if [ -z "${OPENAI_BASE_URL:-}" ]; then
    if [ -n "${DEEPSEEK_API_KEY:-}" ] && [ -z "${YANDEX_API_KEY:-}" ]; then
      export OPENAI_BASE_URL="https://api.deepseek.com"
    else
      export OPENAI_BASE_URL="https://ai.api.cloud.yandex.net/v1"
    fi
  fi
  if [ -n "${YANDEX_API_KEY:-}" ] && [ -z "${OPENAI_API_KEY:-}" ]; then
    export OPENAI_API_KEY="$YANDEX_API_KEY"
  fi
  if [ -n "${YANDEX_FOLDER_ID:-}" ] && [ -z "${OPENAI_PROJECT:-}" ]; then
    export OPENAI_PROJECT="$YANDEX_FOLDER_ID"
  fi
  if [ -n "${DEEPSEEK_API_KEY:-}" ] && [ -z "${OPENAI_API_KEY:-}" ]; then
    export OPENAI_API_KEY="$DEEPSEEK_API_KEY"
  fi
  HERMES_START_COMMAND="${HERMES_START_COMMAND:-python scripts/run-hermes-gateway.py}"
  echo "[BOOT] starting Hermes gateway api_server=${API_SERVER_HOST}:${API_SERVER_PORT}"
  sh -c "$HERMES_START_COMMAND" &
fi

echo "[BOOT] starting uvicorn"
exec uvicorn rental_manager.main:app --host 0.0.0.0 --port "$PORT_VALUE"
