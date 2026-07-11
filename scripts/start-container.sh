#!/usr/bin/env sh
set -eu

PORT_VALUE="${PORT:-10000}"
echo "[BOOT] rental-manager starting port=${PORT_VALUE} db_configured=$([ -n "${RENTAL_MANAGER_DATABASE_URL:-}${DATABASE_URL:-}" ] && echo true || echo false) telegram_env_token_configured=$([ -n "${TELEGRAM_BOT_TOKEN:-}" ] && echo true || echo false)"

AI_PROVIDER_VALUE="$(printf '%s' "${AI_PROVIDER:-}" | tr '[:upper:]-' '[:lower:]_')"
if [ -z "$AI_PROVIDER_VALUE" ]; then
  AI_DIRECT_YANDEX_VALUE="$(printf '%s' "${AI_DIRECT_YANDEX:-1}" | tr '[:upper:]' '[:lower:]')"
  if [ -n "${HERMES_INFERENCE_PROVIDER:-}${AMVERA_LLM_API_KEY:-}${DEEPSEEK_API_KEY:-}${OPENAI_COMPATIBLE_API_KEY:-}" ]; then
    AI_PROVIDER_VALUE="hermes"
  elif [ -n "${YANDEX_AI_API_KEY:-}${YANDEX_API_KEY:-}" ] && [ "$AI_DIRECT_YANDEX_VALUE" != "0" ] && [ "$AI_DIRECT_YANDEX_VALUE" != "false" ] && [ "$AI_DIRECT_YANDEX_VALUE" != "off" ]; then
    AI_PROVIDER_VALUE="yandex"
  else
    AI_PROVIDER_VALUE="hermes"
  fi
fi
echo "[BOOT] ai_provider=${AI_PROVIDER_VALUE} fallback=${AI_FALLBACK_PROVIDER:-none} yandex_key_configured=$([ -n "${YANDEX_AI_API_KEY:-}${YANDEX_API_KEY:-}" ] && echo true || echo false) amvera_llm_key_configured=$([ -n "${AMVERA_LLM_API_KEY:-}" ] && echo true || echo false)"

echo "[BOOT] applying database migrations"
alembic upgrade head

HERMES_ENABLED_VALUE="$(printf '%s' "${HERMES_ENABLED:-true}" | tr '[:upper:]' '[:lower:]')"
if [ "$AI_PROVIDER_VALUE" = "hermes" ] && [ "$HERMES_ENABLED_VALUE" != "0" ] && [ "$HERMES_ENABLED_VALUE" != "false" ] && [ "$HERMES_ENABLED_VALUE" != "off" ]; then
  if [ -z "${HERMES_API_KEY:-}" ]; then
    echo "[BOOT] Hermes selected but HERMES_API_KEY is empty; gateway will not start"
  else
  export API_SERVER_ENABLED="${API_SERVER_ENABLED:-true}"
  export API_SERVER_KEY="${API_SERVER_KEY:-$HERMES_API_KEY}"
  export API_SERVER_HOST="${API_SERVER_HOST:-127.0.0.1}"
  export API_SERVER_PORT="${API_SERVER_PORT:-8642}"
  export API_SERVER_MODEL_NAME="${API_SERVER_MODEL_NAME:-hermes-agent}"
  HERMES_START_COMMAND="${HERMES_START_COMMAND:-python scripts/run-hermes-gateway.py}"
  echo "[BOOT] starting Hermes gateway api_server=${API_SERVER_HOST}:${API_SERVER_PORT} inference_provider=${HERMES_INFERENCE_PROVIDER:-auto}"
  sh -c "$HERMES_START_COMMAND" &
  fi
else
  echo "[BOOT] Hermes gateway not required for ai_provider=${AI_PROVIDER_VALUE}"
fi

echo "[BOOT] starting uvicorn"
exec uvicorn rental_manager.main:app --host 0.0.0.0 --port "$PORT_VALUE"
