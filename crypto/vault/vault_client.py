"""
Vault client cho Enc2Health.
Cung cấp: get_department_public_key, get_dek, get_private_key (Enclave only).
DEK được lưu dưới dạng wrapped ciphertext trong KV-v2 và unwrap qua Vault Transit.
"""
import os
import hvac
import base64
from datetime import datetime, timezone
from functools import lru_cache

VAULT_ADDR  = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200")
VAULT_TRANSIT_KEY = os.getenv("VAULT_TRANSIT_KEY", "enc2health-transit")


def _dek_context(key_name: str) -> str:
    return base64.b64encode(f"enc2health:dek:{key_name}".encode("utf-8")).decode("ascii")


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


def _decrypt_wrapped_dek(client: hvac.Client, key_name: str, secret_data: dict) -> bytes:
    ciphertext = secret_data.get("ciphertext")
    if not ciphertext:
        raise RuntimeError(f"Wrapped DEK for {key_name} is missing ciphertext")

    transit_key = secret_data.get("transit_key") or VAULT_TRANSIT_KEY
    context = secret_data.get("context") or _dek_context(key_name)
    response = client.write(
        f"transit/decrypt/{transit_key}",
        ciphertext=ciphertext,
        context=context,
    )
    plaintext_b64 = response.get("data", {}).get("plaintext")
    if not plaintext_b64:
        raise RuntimeError(f"Transit unwrap for {key_name} returned no plaintext")
    return base64.b64decode(plaintext_b64)


def wrap_dek_bytes(raw_key: bytes, key_name: str, transit_key: str | None = None) -> dict:
    """Wrap raw DEK bytes with Vault Transit and return metadata for KV storage."""
    client = _client()
    transit_key = transit_key or VAULT_TRANSIT_KEY
    response = client.write(
        f"transit/encrypt/{transit_key}",
        plaintext=base64.b64encode(raw_key).decode("ascii"),
        context=_dek_context(key_name),
    )
    ciphertext = response.get("data", {}).get("ciphertext")
    if not ciphertext:
        raise RuntimeError(f"Transit wrap for {key_name} returned no ciphertext")
    return {
        "ciphertext": ciphertext,
        "transit_key": transit_key,
        "context": _dek_context(key_name),
        "wrapped_with": "vault-transit",
    }


def store_wrapped_dek(
    key_name: str,
    raw_key: bytes,
    algorithm: str,
    purpose: str,
    transit_key: str | None = None,
) -> dict:
    """Store a Transit-wrapped DEK under enc2health/dek/<key_name>."""
    client = _client()
    wrapped = wrap_dek_bytes(raw_key, key_name, transit_key=transit_key)
    wrapped["algorithm"] = algorithm
    wrapped["purpose"] = purpose
    wrapped["stored_at"] = datetime.now(timezone.utc).isoformat()
    client.secrets.kv.v2.create_or_update_secret(
        path=f"dek/{key_name}",
        secret=wrapped,
        mount_point="enc2health",
    )
    return wrapped


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
    data = secret["data"]["data"]
    if "ciphertext" in data:
        return _decrypt_wrapped_dek(c, key_name, data)
    if "key" in data:
        return base64.b64decode(data["key"])
    raise RuntimeError(f"DEK record for {key_name} has no wrapped or plaintext key field")


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
