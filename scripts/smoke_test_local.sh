#!/usr/bin/env bash
set -euo pipefail

AUTH_JWT_SECRET=${AUTH_JWT_SECRET:-dev-secret-32-bytes-long-1234567890}
export AUTH_JWT_SECRET

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
CERT_DIR="$REPO_ROOT/certs"
VENV_DIR="$REPO_ROOT/.venv"
VENV_PY="$VENV_DIR/bin/python"

mkdir -p "$CERT_DIR"

# Requested pool data policy: auto | mongo | mock
REQUESTED_DATA_MODE=${T8_POOL_DATA_MODE:-auto}
if [[ "$REQUESTED_DATA_MODE" != "auto" && "$REQUESTED_DATA_MODE" != "mongo" && "$REQUESTED_DATA_MODE" != "mock" ]]; then
  echo "[smoke-local] invalid T8_POOL_DATA_MODE=$REQUESTED_DATA_MODE (expected: auto|mongo|mock)"
  exit 1
fi

DBPATH=$(mktemp -d /tmp/enc2health-mongo-db.XXXX)
MONGO_PORT=${MONGO_PORT:-27017}
MONGO_URI=${MONGO_URI:-mongodb://127.0.0.1:${MONGO_PORT}}

cleanup() {
  echo "[smoke-local] cleaning up..."
  [[ -n "${ROUTER_PID-}" ]] && kill "$ROUTER_PID" 2>/dev/null || true
  [[ -n "${ECALL_PID-}" ]] && kill "$ECALL_PID" 2>/dev/null || true
  [[ -n "${MONGOD_PID-}" ]] && kill "$MONGOD_PID" 2>/dev/null || true
  rm -rf "$DBPATH"
}

if [[ "${KEEP_ALIVE:-0}" != "1" ]]; then
  trap cleanup EXIT
else
  echo "[smoke-local] KEEP_ALIVE=1 set; processes will not be killed automatically on script exit"
fi

# Generate development CA/server/client certs for local mTLS if not present
if [[ "${REGEN_CERTS:-0}" == "1" || ! ( -f "$CERT_DIR/ca.crt" && -f "$CERT_DIR/server.crt" && -f "$CERT_DIR/client.crt" ) ]]; then
  if ! command -v openssl >/dev/null 2>&1; then
    echo "[smoke-local] openssl is required to generate dev certs; please install openssl or provide certs in $CERT_DIR"
    exit 1
  fi
  echo "[smoke-local] generating dev CA and mTLS certs in $CERT_DIR"
  set -x
  openssl genrsa -out "$CERT_DIR/ca.key" 2048
  openssl req -x509 -new -nodes -key "$CERT_DIR/ca.key" -sha256 -days 365 -subj "/CN=enc2health-local-CA" -out "$CERT_DIR/ca.crt"

  openssl genrsa -out "$CERT_DIR/server.key" 2048
  cat > "$CERT_DIR/server_ext.cnf" <<'EOF'
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage = digitalSignature, nonRepudiation, keyEncipherment, dataEncipherment
subjectAltName = @alt_names

[alt_names]
DNS.1 = localhost
DNS.2 = ecall-pool
IP.1 = 127.0.0.1
EOF
  openssl req -new -key "$CERT_DIR/server.key" -out "$CERT_DIR/server.csr" \
    -subj "/C=VN/ST=HCM/L=ThuDuc/O=UIT/OU=NT219/CN=127.0.0.1"
  openssl x509 -req -in "$CERT_DIR/server.csr" -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" \
    -CAcreateserial -out "$CERT_DIR/server.crt" -days 365 -sha256 -extfile "$CERT_DIR/server_ext.cnf"

  openssl genrsa -out "$CERT_DIR/client.key" 2048
  openssl req -new -key "$CERT_DIR/client.key" -subj "/CN=router-client" -out "$CERT_DIR/client.csr"
  openssl x509 -req -in "$CERT_DIR/client.csr" -CA "$CERT_DIR/ca.crt" -CAkey "$CERT_DIR/ca.key" \
    -CAcreateserial -out "$CERT_DIR/client.crt" -days 365 -sha256
  set +x
fi

export T8_SSL_CERT="$CERT_DIR/server.crt"
export T8_SSL_KEY="$CERT_DIR/server.key"
export T8_SSL_CA="$CERT_DIR/ca.crt"
export ROUTER_CLIENT_CERT="$CERT_DIR/client.crt"
export ROUTER_CLIENT_KEY="$CERT_DIR/client.key"
export ECALL_POOL_URL="https://127.0.0.1:9091"

echo "[smoke-local] certs in $CERT_DIR; T8_SSL_CERT=$T8_SSL_CERT"
echo "[smoke-local] Using AUTH_JWT_SECRET=${AUTH_JWT_SECRET}"

# Start local mongod if available and mode allows it
STARTED_LOCAL_MONGO=0
if command -v mongod >/dev/null 2>&1 && [[ "$REQUESTED_DATA_MODE" != "mock" ]]; then
  echo "[smoke-local] starting temporary mongod at ${MONGO_PORT}, dbpath=${DBPATH}"
  mkdir -p "$DBPATH"
  mongod --port "$MONGO_PORT" --dbpath "$DBPATH" --bind_ip 127.0.0.1 --quiet &
  MONGOD_PID=$!
  STARTED_LOCAL_MONGO=1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[smoke-local] creating virtualenv in $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

echo "[smoke-local] bootstrapping pip in venv and installing Python requirements (this may take a moment)"
"$VENV_PY" -m ensurepip --upgrade || true
"$VENV_PY" -m pip install --upgrade pip setuptools wheel || true
"$VENV_PY" -m pip install -r "$REPO_ROOT/crypto/requirements.txt"

# Decide effective mode and fail-fast if mongo was explicitly required but unavailable.
EFFECTIVE_DATA_MODE="$REQUESTED_DATA_MODE"
if [[ "$REQUESTED_DATA_MODE" == "auto" ]]; then
  EFFECTIVE_DATA_MODE="mock"
fi

MONGO_REACHABLE=0
if [[ "$REQUESTED_DATA_MODE" != "mock" ]]; then
  if "$VENV_PY" - <<PY >/dev/null 2>&1
from pymongo import MongoClient
c = MongoClient('${MONGO_URI}', serverSelectionTimeoutMS=1500, connectTimeoutMS=1500, socketTimeoutMS=1500)
c.admin.command('ping')
print('ok')
PY
  then
    MONGO_REACHABLE=1
  fi
fi

if [[ "$REQUESTED_DATA_MODE" == "mongo" && "$MONGO_REACHABLE" -ne 1 ]]; then
  echo "[smoke-local] FAIL-FAST: T8_POOL_DATA_MODE=mongo nhưng không kết nối được MongoDB tại MONGO_URI=${MONGO_URI}"
  echo "[smoke-local] Hãy bật mongod hoặc cung cấp MONGO_URI hợp lệ trước khi chạy lại."
  exit 2
fi

if [[ "$REQUESTED_DATA_MODE" == "auto" && "$MONGO_REACHABLE" -eq 1 ]]; then
  EFFECTIVE_DATA_MODE="mongo"
fi

if [[ "$EFFECTIVE_DATA_MODE" == "mock" ]]; then
  echo "[smoke-local][COMPLIANCE/SECURITY NOTE] Running in MOCK mode."
  echo "[smoke-local][COMPLIANCE/SECURITY NOTE] MOCK mode is ONLY allowed in isolated local dev."
  echo "[smoke-local][COMPLIANCE/SECURITY NOTE] DO NOT use MOCK mode in production or regulated environments (HIPAA/GDPR)."
  SKIP_SEED=1
  # Force mongo init failure in pool so it uses mock path deterministically.
  MONGO_URI=""
else
  SKIP_SEED=0
fi

KEY_DIR="$REPO_ROOT/crypto/data/keys"
mkdir -p "$KEY_DIR"

if [[ "$SKIP_SEED" -eq 0 ]]; then
  echo "[smoke-local] seeding data into MongoDB..."
  MONGO_URI=${MONGO_URI} MONGO_DB=enc2health MONGO_COLLECTION=patient_records EHR_RECORD_COUNT=100 EHR_BATCH_SIZE=50 EHR_FORCE_RECREATE=1 \
    "$VENV_PY" crypto/data/generate_ehr.py
else
  echo "[smoke-local] skipping seeding. ECALL pool will use mock dataset."
fi

echo "[smoke-local] starting ecall task pool (with TLS, data_mode=$EFFECTIVE_DATA_MODE)"
T8_POOL_HOST=127.0.0.1 T8_POOL_PORT=9091 T8_POOL_DATA_MODE="$EFFECTIVE_DATA_MODE" MONGO_URI=${MONGO_URI} \
  T8_SSL_CERT="$T8_SSL_CERT" T8_SSL_KEY="$T8_SSL_KEY" T8_SSL_CA="$T8_SSL_CA" \
  "$VENV_PY" enclave/ecall_pool.py &
ECALL_PID=$!

echo "[smoke-local] starting router (uvicorn)"
ECALL_POOL_URL="$ECALL_POOL_URL" ROUTER_CLIENT_CERT="$ROUTER_CLIENT_CERT" ROUTER_CLIENT_KEY="$ROUTER_CLIENT_KEY" T8_SSL_CA="$T8_SSL_CA" \
  "$VENV_PY" -m uvicorn router.main:app --host 127.0.0.1 --port 8000 &
ROUTER_PID=$!

echo "[smoke-local] waiting for router health"
for i in {1..30}; do
  if curl -sSf http://127.0.0.1:8000/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -sSf http://127.0.0.1:8000/health >/dev/null 2>&1; then
  echo "[smoke-local] router failed to become healthy"
  exit 3
fi

echo "[smoke-local] generating JWT and performing sample query"
TOKEN=$("$VENV_PY" - <<PY
from common.auth import generate_test_jwt
print(generate_test_jwt('smoke-local', role='admin'))
PY
)

RESP=$(curl -sS -w "\n%{http_code}" -X POST http://127.0.0.1:8000/query \
  -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
  -d '{"query_type":"sum_vien_phi","filters":{}}' || true)
BODY=$(echo "$RESP" | sed '$d')
CODE=$(echo "$RESP" | tail -n1)
echo "[smoke-local] Router returned HTTP $CODE"
echo "$BODY"

if [[ "$CODE" != "200" ]]; then
  echo "[smoke-local] sample query failed"
  exit 4
fi

echo "[smoke-local] sample query succeeded"
exit 0
