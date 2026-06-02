#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8008}"
LOG_FILE="${LOG_FILE:-/tmp/uvicorn8008.log}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:${PORT}/health}"
WAIT_SECONDS="${WAIT_SECONDS:-20}"

cd "$ROOT_DIR"

set -a
[[ -f ".env" ]] && source .env
set +a

if [[ -f ".venv/bin/activate" ]]; then
  source .venv/bin/activate
  UVICORN_CMD=(python -m uvicorn)
  PYTHON_RUNTIME=".venv"
else
  echo "missing virtualenv: $ROOT_DIR/.venv; falling back to current python3/uvicorn"
  PYTHON_RUNTIME="python3"
  if python3 -c "import uvicorn" >/dev/null 2>&1; then
    UVICORN_CMD=(python3 -m uvicorn)
  elif command -v uvicorn >/dev/null 2>&1; then
    UVICORN_CMD=(uvicorn)
    PYTHON_RUNTIME="uvicorn command"
  else
    echo "missing uvicorn: install uvicorn in .venv or make python3/uvicorn available"
    exit 1
  fi
fi

export GATEWAY_VISIBLE_THINKING_ENABLED="${GATEWAY_VISIBLE_THINKING_ENABLED:-1}"
export GATEWAY_STREAM_PROCESS_FORMAT="${GATEWAY_STREAM_PROCESS_FORMAT:-openwebui}"
export GATEWAY_LIVE_THINKING_ENABLED="${GATEWAY_LIVE_THINKING_ENABLED:-1}"
export GATEWAY_LIVE_THINKING_DELTA_MODE="${GATEWAY_LIVE_THINKING_DELTA_MODE:-reasoning_content}"

pkill -f "uvicorn app:app --host ${HOST} --port ${PORT}" 2>/dev/null || true
pkill -f "uvicorn app:app" 2>/dev/null || true

for ((i = 1; i <= 10; i++)); do
  if ! lsof -ti "tcp:${PORT}" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

nohup "${UVICORN_CMD[@]}" app:app --host "$HOST" --port "$PORT" --log-level info >"$LOG_FILE" 2>&1 &
PID=$!
disown "$PID" 2>/dev/null || true

echo "gateway starting, pid=$PID, log=$LOG_FILE"
echo "python runtime: ${PYTHON_RUNTIME}"
echo "visible thinking enabled: ${GATEWAY_VISIBLE_THINKING_ENABLED}"
echo "stream process format: ${GATEWAY_STREAM_PROCESS_FORMAT}"
echo "live thinking enabled: ${GATEWAY_LIVE_THINKING_ENABLED}"
echo "live thinking delta mode: ${GATEWAY_LIVE_THINKING_DELTA_MODE}"
echo "school agent model: ${SCHOOL_AGENT_NATIVE_MODEL_ID:-${TENANT_AGENT_NATIVE_MODEL_ID:-default}}"

for ((i = 1; i <= WAIT_SECONDS; i++)); do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "gateway process exited before health check succeeded: pid=$PID"
    echo "last log lines:"
    tail -n 80 "$LOG_FILE" || true
    exit 1
  fi
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    echo "gateway healthy: $HEALTH_URL"
    exit 0
  fi
  sleep 1
done

echo "gateway failed health check within ${WAIT_SECONDS}s"
echo "last log lines:"
tail -n 60 "$LOG_FILE" || true
exit 1
