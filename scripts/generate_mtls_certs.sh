#!/usr/bin/env bash
# Generate a CA, server cert, and client cert for local mTLS testing.
set -euo pipefail

OUT_DIR="${1:-certs}"
mkdir -p "$OUT_DIR"

CA_SUBJECT="/CN=enc2health-ca"
SERVER_SUBJECT="/CN=ecall-task-pool"
CLIENT_SUBJECT="/CN=router-client"

SERVER_SAN="DNS:ecall-task-pool,DNS:localhost,IP:127.0.0.1"
CLIENT_SAN="DNS:router-client,DNS:localhost,IP:127.0.0.1"

cat > "$OUT_DIR/server-openssl.cnf" <<EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = ecall-task-pool

[v3_req]
subjectAltName = $SERVER_SAN
extendedKeyUsage = serverAuth
keyUsage = critical, digitalSignature, keyEncipherment
EOF

cat > "$OUT_DIR/client-openssl.cnf" <<EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = router-client

[v3_req]
subjectAltName = $CLIENT_SAN
extendedKeyUsage = clientAuth
keyUsage = critical, digitalSignature, keyEncipherment
EOF

echo "Generating CA..."
openssl genrsa -out "$OUT_DIR/ca.key" 4096
openssl req -x509 -new -nodes -key "$OUT_DIR/ca.key" -sha256 -days 3650 -subj "$CA_SUBJECT" -out "$OUT_DIR/ca.crt"

echo "Generating server key and CSR..."
openssl genrsa -out "$OUT_DIR/server.key" 4096
openssl req -new -key "$OUT_DIR/server.key" -subj "$SERVER_SUBJECT" -out "$OUT_DIR/server.csr"

echo "Signing server cert with CA..."
openssl x509 -req -in "$OUT_DIR/server.csr" -CA "$OUT_DIR/ca.crt" -CAkey "$OUT_DIR/ca.key" -CAcreateserial -out "$OUT_DIR/server.crt" -days 365 -sha256 -extfile "$OUT_DIR/server-openssl.cnf" -extensions v3_req

echo "Generating client key and CSR..."
openssl genrsa -out "$OUT_DIR/client.key" 4096
openssl req -new -key "$OUT_DIR/client.key" -subj "$CLIENT_SUBJECT" -out "$OUT_DIR/client.csr"

echo "Signing client cert with CA..."
openssl x509 -req -in "$OUT_DIR/client.csr" -CA "$OUT_DIR/ca.crt" -CAkey "$OUT_DIR/ca.key" -CAcreateserial -out "$OUT_DIR/client.crt" -days 365 -sha256 -extfile "$OUT_DIR/client-openssl.cnf" -extensions v3_req

chmod 600 "$OUT_DIR"/*.key

echo "Generated certs in $OUT_DIR"
echo "Use these env vars to run services:"
echo "  T8_SSL_CERT=$OUT_DIR/server.crt"
echo "  T8_SSL_KEY=$OUT_DIR/server.key"
echo "  T8_SSL_CA=$OUT_DIR/ca.crt"
echo "  ROUTER_CLIENT_CERT=$OUT_DIR/client.crt"
echo "  ROUTER_CLIENT_KEY=$OUT_DIR/client.key"
