"""
Vault client cho Enc2Health.
Cung cấp: get_department_public_key, get_dek, get_private_key (Enclave only)
"""
import os
import hvac
import base64
from functools import lru_cache

VAULT_ADDR  = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200")


def _login_with_approle(client: hvac.Client) -> str:
    role_id = os.getenv("VAULT_ROLE_ID")
    secret_id = os.getenv("VAULT_SECRET_ID")
    if not role_id or not secret_id:
        raise RuntimeError("No VAULT_TOKEN and no VAULT_ROLE_ID/VAULT_SECRET_ID provided")

    response = client.auth.approle.login(role_id=role_id, secret_id=secret_id)
    token = response.get("auth", {}).get("client_token")
    if not token:
        raise RuntimeError("Vault AppRole login failed (no token returned)")
    return token

def _client() -> hvac.Client:
    c = hvac.Client(url=VAULT_ADDR)
    # Prefer explicit token env; otherwise attempt AppRole login.
    # AppRole is the production path for enclave startup.
    vault_token = os.getenv("VAULT_TOKEN")
    if vault_token:
        c.token = vault_token
    else:
        c.token = _login_with_approle(c)

    if not c.is_authenticated():
        raise RuntimeError("Vault authentication failed! Check VAULT_ADDR and credentials.")
    return c


@lru_cache(maxsize=32)
def get_department_public_key(dept: str) -> bytes:
    """Lấy public key của Khoa. Cache vì không thay đổi thường xuyên."""
    c = _client()
    secret = c.secrets.kv.v2.read_secret_version(
        path=f"keypairs/{dept}",
        mount_point="enc2health"
    )
    return secret["data"]["data"]["public_key"].encode()


def get_department_private_key(dept: str) -> bytes:
    """
    Lấy private key. CHỈ gọi trong Enclave context.
    Trong production: Enclave phải đã xác thực qua RA-TLS trước khi gọi.
    """
    c = _client()
    secret = c.secrets.kv.v2.read_secret_version(
        path=f"keypairs/{dept}",
        mount_point="enc2health"
    )
    return secret["data"]["data"]["private_key"].encode()


def get_dek(key_name: str) -> bytes:
    """Lấy DEK từ Vault. key_name: 'gcm_dek', 'dte_ma_benh', 'ore_key', ..."""
    c = _client()
    secret = c.secrets.kv.v2.read_secret_version(
        path=f"dek/{key_name}",
        mount_point="enc2health"
    )
    return base64.b64decode(secret["data"]["data"]["key"])


def list_departments() -> list[str]:
    c = _client()
    result = c.secrets.kv.v2.list_secrets(
        path="keypairs",
        mount_point="enc2health"
    )
    return [k.rstrip("/") for k in result["data"]["keys"]]


if __name__ == "__main__":
    print("=== Vault Client Test ===")
    depts = list_departments()
    print(f"  Departments in Vault: {depts}")

    pub = get_department_public_key("Noi")
    print(f"  Public key (Noi): {pub[:50]}...")

    gcm_key = get_dek("gcm_dek")
    print(f"  GCM DEK length: {len(gcm_key)} bytes ✓")
    print("All OK!")
