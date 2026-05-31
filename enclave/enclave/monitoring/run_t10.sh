#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="$SCRIPT_DIR/exporter.log"
PID_FILE="$SCRIPT_DIR/exporter.pid"

PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

echo "[T10] Using Python: $PYTHON_BIN"

metrics_ready() {
  curl -fsS --max-time 1 http://127.0.0.1:8002/metrics >/dev/null 2>&1
}

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" || true)"
  if [[ -n "${OLD_PID:-}" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID" 2>/dev/null || true
    sleep 0.2
  fi
fi

if metrics_ready; then
  echo "[T10] Exporter already responding on port 8002; reusing existing process"
  REUSED_EXISTING=1
else
  REUSED_EXISTING=0
  : > "$LOG_FILE"
  nohup "$PYTHON_BIN" "$SCRIPT_DIR/exporter.py" > "$LOG_FILE" 2>&1 &
  EXPORTER_PID=$!
  echo "$EXPORTER_PID" > "$PID_FILE"
  echo "[T10] Exporter PID: $EXPORTER_PID"

  sleep 1
  if ! metrics_ready; then
    echo "[T10] Exporter did not become ready; showing log"
    echo ""
    echo "[T10] Exporter log"
    tail -n 50 "$LOG_FILE" || true
  fi
fi

if [[ "$REUSED_EXISTING" -eq 0 ]]; then
  echo ""
  echo "[T10] Exporter log"
  tail -n 50 "$LOG_FILE" || true
fi

if command -v docker >/dev/null 2>&1; then
  echo ""
  echo "[T10] Starting Prometheus + Grafana stack"
  if docker compose version >/dev/null 2>&1; then
    (cd "$SCRIPT_DIR" && docker compose up -d)
    (cd "$SCRIPT_DIR" && docker compose ps)
  elif command -v docker-compose >/dev/null 2>&1; then
    (cd "$SCRIPT_DIR" && docker-compose up -d)
    (cd "$SCRIPT_DIR" && docker-compose ps)
  else
    echo "[T10] docker is available but compose is missing"
  fi
else
  echo ""
  echo "[T10] Docker is not installed on this machine; skipping Prometheus/Grafana stack"
fi

echo ""
echo "[T10] Exporter metrics"
curl -s http://127.0.0.1:8002/metrics | sed -n '1,120p'

echo ""
echo "[T10] Done"