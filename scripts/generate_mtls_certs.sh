#!/usr/bin/env bash
# Generate a CA, server cert, and client cert for mTLS testing.
set -euo pipefail
OUT_DIR="certs"
mkdir -p "$OUT_DIR"

echo "Generating CA..."
openssl genrsa -out "$OUT_DIR/ca.key" 4096
openssl req -x509 -new -nodes -key "$OUT_DIR/ca.key" -sha256 -days 3650 -subj "/CN=enc2health-ca" -out "$OUT_DIR/ca.crt"

echo "Generating server key and CSR..."
openssl genrsa -out "$OUT_DIR/server.key" 4096
openssl req -new -key "$OUT_DIR/server.key" -subj "/CN=ecall-pool" -out "$OUT_DIR/server.csr"

echo "Signing server cert with CA..."
openssl x509 -req -in "$OUT_DIR/server.csr" -CA "$OUT_DIR/ca.crt" -CAkey "$OUT_DIR/ca.key" -CAcreateserial -out "$OUT_DIR/server.crt" -days 365 -sha256

echo "Generating client key and CSR..."
openssl genrsa -out "$OUT_DIR/client.key" 4096
openssl req -new -key "$OUT_DIR/client.key" -subj "/CN=router-client" -out "$OUT_DIR/client.csr"

echo "Signing client cert with CA..."
openssl x509 -req -in "$OUT_DIR/client.csr" -CA "$OUT_DIR/ca.crt" -CAkey "$OUT_DIR/ca.key" -CAcreateserial -out "$OUT_DIR/client.crt" -days 365 -sha256

echo "Generated certs in $OUT_DIR"
echo "Use these env vars to run services:"
echo "  T8_SSL_CERT=$OUT_DIR/server.crt"
echo "  T8_SSL_KEY=$OUT_DIR/server.key"
echo "  T8_SSL_CA=$OUT_DIR/ca.crt"
echo "  ROUTER_CLIENT_CERT=$OUT_DIR/client.crt"
echo "  ROUTER_CLIENT_KEY=$OUT_DIR/client.key"
