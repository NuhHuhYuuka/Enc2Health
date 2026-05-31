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
vault kv put enc2health/dek/gcm_dek \
    key=@$KEY_DIR/gcm_dek.key \
    algorithm="AES-GCM-256" \
    purpose="lab_and_billing"

vault kv put enc2health/dek/dte_ma_benh \
    key=@$KEY_DIR/dte_ma_benh.key \
    algorithm="AES-SIV-256" \
    purpose="icd10_equality_search"

vault kv put enc2health/dek/dte_khoa \
    key=@$KEY_DIR/dte_khoa.key \
    algorithm="AES-SIV-256" \
    purpose="department_equality_search"

vault kv put enc2health/dek/ore_key \
    key=@$KEY_DIR/ore.key \
    algorithm="OPE-Boldyreva" \
    purpose="age_date_range_query"

echo "=== [4/4] Tạo Vault policy cho Enclave (Lan) ==="
cat <<'POLICY' | vault policy write enclave-policy -
path "enc2health/keypairs/*" {
  capabilities = ["read"]
}
path "enc2health/dek/*" {
  capabilities = ["read"]
}
POLICY

cat <<'POLICY' | vault policy write kms-api-policy -
path "enc2health/dek/*" {
  capabilities = ["read"]
}
path "enc2health/keypairs/*/public_key" {
  capabilities = ["read"]
}
POLICY

echo ""
echo "✅ Vault setup complete!"
echo "   List secrets: vault kv list enc2health/keypairs/"
echo "   Get keypair:  vault kv get enc2health/keypairs/Noi"
