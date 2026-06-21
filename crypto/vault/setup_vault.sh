#!/bin/bash
# ~/enc2health/vault/setup_vault.sh
# Setup HashiCorp Vault cho Enc2Health

set -e
export VAULT_ADDR='http://127.0.0.1:8200'
export VAULT_TOKEN='enc2health-root-token'

# Bộ key DÙNG CHUNG, đường dẫn tuyệt đối (độc lập cwd): crypto/data/keys/
KEY_DIR="$(cd "$(dirname "$0")/../data/keys" && pwd)"

echo "=== [1/4] Enable KV-v2 secrets engine ==="
vault secrets enable -path=enc2health kv-v2 2>/dev/null || echo "  Already enabled"

echo "=== [1b/4] Enable Vault Transit engine ==="
vault secrets enable -path=transit transit 2>/dev/null || echo "  Already enabled"
vault write -f transit/keys/enc2health-transit >/dev/null

echo "=== [2/4] Upload keypairs cho từng Khoa ==="
DEPARTMENTS=("Noi" "Ngoai" "Cap_cuu" "Tim_mach" "Than_kinh" "Nhi")
for dept in "${DEPARTMENTS[@]}"; do
    PRIV_FILE="$KEY_DIR/${dept}_private.pem"
    PUB_FILE="$KEY_DIR/${dept}_public.pem"
    if [ -f "$PRIV_FILE" ] && [ -f "$PUB_FILE" ]; then
        vault kv put "enc2health/keypairs/${dept}" \
            private_key=@"$PRIV_FILE" \
            public_key=@"$PUB_FILE" \
            algorithm="ECC_P384" \
            department="$dept"
        echo "  ✓ Uploaded keypair for: $dept"
    else
        echo "  ⚠ Missing key files for: $dept (chạy generate_ehr.py trước)"
    fi
done

echo "=== [3/4] Upload DEK (AES-GCM) và DTE keys ==="
wrap_and_store_dek() {
  local key_name="$1"
  local key_file="$2"
  local algorithm="$3"
  local purpose="$4"
  local context_b64
  local plaintext_b64
  local ciphertext
  context_b64=$(printf 'enc2health:dek:%s' "$key_name" | base64 | tr -d '\n')
  plaintext_b64=$(base64 -d "$key_file" | base64 -w0)
  ciphertext=$(vault write -field=ciphertext transit/encrypt/enc2health-transit \
    plaintext="$plaintext_b64" \
    context="$context_b64")
  vault kv put enc2health/dek/"$key_name" \
    ciphertext="$ciphertext" \
    transit_key="enc2health-transit" \
    context="$context_b64" \
    wrapped_with="vault-transit" \
    algorithm="$algorithm" \
    purpose="$purpose"
}

wrap_and_store_dek gcm_dek "$KEY_DIR/gcm_dek.key" "AES-GCM-256" "lab_and_billing"

wrap_and_store_dek dte_ma_benh "$KEY_DIR/dte_ma_benh.key" "AES-SIV-256" "icd10_equality_search"

wrap_and_store_dek dte_khoa "$KEY_DIR/dte_khoa.key" "AES-SIV-256" "department_equality_search"

wrap_and_store_dek dte_cmnd "$KEY_DIR/dte_cmnd.key" "AES-SIV-256" "citizen_id_equality_search"

wrap_and_store_dek ore_key "$KEY_DIR/ore.key" "OPE-Boldyreva" "age_date_range_query"

wrap_and_store_dek sse_key "$KEY_DIR/sse.key" "HMAC-SHA256+AES-GCM" "clinical_keyword_search"

echo "=== [4/4] Tạo Vault policy và AppRole cho Enclave (Lan) ==="
cat <<'POLICY' | vault policy write enclave-policy -
path "enc2health/data/keypairs/*" {
  capabilities = ["read"]
}
path "enc2health/data/dek/*" {
  capabilities = ["read"]
}
path "transit/encrypt/enc2health-transit" {
  capabilities = ["update"]
}
path "transit/decrypt/enc2health-transit" {
  capabilities = ["update"]
}
POLICY

vault auth enable approle 2>/dev/null || echo "  AppRole already enabled"
vault write auth/approle/role/enc2health-enclave \
  token_policies="enclave-policy" \
  token_ttl="1h" \
  token_max_ttl="4h" >/dev/null

ROLE_ID=$(vault read -field=role_id auth/approle/role/enc2health-enclave/role-id)
SECRET_ID=$(vault write -f -field=secret_id auth/approle/role/enc2health-enclave/secret-id)
printf '%s\n' "$ROLE_ID" > /tmp/enc2health-vault-role-id
printf '%s\n' "$SECRET_ID" > /tmp/enc2health-vault-secret-id
chmod 600 /tmp/enc2health-vault-role-id /tmp/enc2health-vault-secret-id

cat <<'POLICY' | vault policy write kms-api-policy -
path "enc2health/data/dek/*" {
  capabilities = ["read"]
}
path "enc2health/data/keypairs/*" {
  capabilities = ["read"]
}
path "transit/decrypt/enc2health-transit" {
  capabilities = ["update"]
}
POLICY

echo ""
echo "✅ Vault setup complete!"
echo "   List secrets: vault kv list enc2health/keypairs/"
echo "   Get keypair:  vault kv get enc2health/keypairs/Noi"
echo "   AppRole role_id:   /tmp/enc2health-vault-role-id"
echo "   AppRole secret_id: /tmp/enc2health-vault-secret-id"
