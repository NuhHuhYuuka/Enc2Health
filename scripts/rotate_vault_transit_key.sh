#!/usr/bin/env bash
set -euo pipefail
VAULT_ADDR=${VAULT_ADDR:-http://127.0.0.1:8200}
KEY_NAME=${1:-enc2health-transit}

echo "Rotating Vault transit key: $KEY_NAME on $VAULT_ADDR"
if ! command -v vault >/dev/null 2>&1; then
  echo "vault CLI not found. Install HashiCorp Vault CLI to run rotation."
  exit 1
fi

export VAULT_ADDR
echo "Rotate key via Vault CLI (requires appropriate permissions)..."
vault write -f transit/keys/${KEY_NAME}/rotate
echo "Rotation requested. Update applications if using explicit key versions."
