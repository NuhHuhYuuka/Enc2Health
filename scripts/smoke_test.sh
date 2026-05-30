#!/usr/bin/env bash
set -euo pipefail

COMPOSE=${COMPOSE:-docker compose}
AUTH_JWT_SECRET=${AUTH_JWT_SECRET:-dev-secret-32-bytes-long-1234567890}
export AUTH_JWT_SECRET

echo "[smoke] Ensuring certs (via certgen service in compose) and starting stack..."
$COMPOSE up --build -d

echo "[smoke] Waiting for router health (http://127.0.0.1:8000/health)"
TRIES=0
MAX=60
until curl -sSf http://127.0.0.1:8000/health >/dev/null 2>&1; do
  TRIES=$((TRIES+1))
  if [ "$TRIES" -ge "$MAX" ]; then
    echo "[smoke] router healthcheck failed after $MAX tries"
    $COMPOSE logs --no-color --tail=200
    exit 1
  fi
  sleep 2
done

echo "[smoke] router is healthy. Performing sample query via container exec."

echo "[smoke] Generating JWT inside query-router container (role=Chief_Physician)"
TOKEN=$($COMPOSE exec -T query-router python -c "from common.auth import generate_test_jwt; print(generate_test_jwt('smoke-test', role='Chief_Physician'))") || {
  echo "[smoke] failed to generate JWT inside container"
  $COMPOSE logs --no-color --tail=200
  exit 2
}

echo "[smoke] JWT generated. Performing curl to Router /query as Chief_Physician"
HTTP_RESP=$(curl -sS -w "\n%{http_code}" -X POST http://127.0.0.1:8000/query \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"query_type":"count","filters":{}}' || true)

HTTP_BODY=$(echo "$HTTP_RESP" | sed '$d')
HTTP_CODE=$(echo "$HTTP_RESP" | tail -n1)
echo "[smoke] Router response code: $HTTP_CODE"
echo "$HTTP_BODY"

if [ "$HTTP_CODE" != "200" ]; then
  echo "[smoke] sample query failed (http_code=$HTTP_CODE). Dumping logs."
  $COMPOSE logs --no-color --tail=200
  exit 3
fi

echo "[smoke] Sample query succeeded."

echo "[smoke] To tear down the stack: ${COMPOSE} down -v"
