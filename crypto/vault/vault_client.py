"""
Vault client cho Enc2Health.
Cung cấp: get_department_public_key, get_dek, get_private_key (Enclave only)
"""
import os
import hvac
import base64
from functools import lru_cache

VAULT_ADDR  = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200")
VAULT_TOKEN = os.getenv("VAULT_TOKEN", "enc2health-root-token")

def _client() -> hvac.Client:
    c = hvac.Client(url=VAULT_ADDR, token=VAULT_TOKEN)
    assert c.is_authenticated(), "Vault authentication failed!"
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
