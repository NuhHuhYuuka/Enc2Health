"""JWT auth dùng chung cho Enc²Health — ký BẤT ĐỐI XỨNG bằng ES256 (ECDSA P-256).

Vì sao ES256 (không phải HS256):
- HS256 đối xứng → mọi node biết secret đều KÝ được token (không tách quyền).
- ES256 (ECDSA P-256) bất đối xứng → CHỈ node Authentication (IAM) giữ PRIVATE key
  để ký; Router/Pool chỉ cần PUBLIC key để VERIFY (không thể tự phát hành token).
  → khớp định hướng "all-ECC, no-RSA" của đề tài.

Khóa nằm ở `crypto/data/keys/` (cùng cơ chế đồng bộ khóa của nhóm):
  - jwt_es256_private.pem  (PKCS8)  — chỉ IAM node cần.
  - jwt_es256_public.pem   (SPKI)   — mọi node verify cần.
Override path bằng env: AUTH_JWT_PRIVATE_KEY / AUTH_JWT_PUBLIC_KEY.

Sinh khóa: `python3 crypto/data/generate_jwt_keys.py` (hoặc tự tạo khi ký lần đầu).
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional, Dict

import jwt
from fastapi import HTTPException

JWT_ALG = "ES256"
JWT_ISS = os.environ.get("AUTH_JWT_ISS", "enc2health")
JWT_AUD = os.environ.get("AUTH_JWT_AUD", "enc2health_services")

_KEYS_DIR = Path(__file__).resolve().parents[1] / "crypto" / "data" / "keys"
_PRIV_PATH = Path(os.environ.get("AUTH_JWT_PRIVATE_KEY", str(_KEYS_DIR / "jwt_es256_private.pem")))
_PUB_PATH = Path(os.environ.get("AUTH_JWT_PUBLIC_KEY", str(_KEYS_DIR / "jwt_es256_public.pem")))

# Cho phép tự tạo keypair khi ký lần đầu (tiện dev). Tắt ở production: chỉ IAM ký.
_ALLOW_KEYGEN = os.environ.get("AUTH_JWT_ALLOW_KEYGEN", "1") == "1"


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text()
    except Exception:
        return None


def _generate_keypair() -> tuple[str, str]:
    """Sinh cặp khóa EC P-256, ghi PEM ra keys dir (load-or-create)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization

    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    try:
        _PRIV_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PRIV_PATH.write_text(priv_pem)
        _PUB_PATH.write_text(pub_pem)
    except Exception:
        pass  # môi trường read-only vẫn dùng được key trong RAM
    return priv_pem, pub_pem


def _private_key() -> Optional[str]:
    pem = _read(_PRIV_PATH)
    if pem:
        return pem
    if _ALLOW_KEYGEN:
        return _generate_keypair()[0]
    return None


def get_public_key_pem() -> Optional[str]:
    """PEM public key để verify (IAM node công bố qua /public-key)."""
    pub = _read(_PUB_PATH)
    if pub:
        return pub
    # Nếu chỉ có private (dev 1 máy) → suy ra public từ private.
    priv = _read(_PRIV_PATH)
    if priv:
        from cryptography.hazmat.primitives import serialization
        key = serialization.load_pem_private_key(priv.encode(), password=None)
        return key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
    if _ALLOW_KEYGEN:
        return _generate_keypair()[1]
    return None


def validate_jwt_bearer(bearer: Optional[str]) -> Dict:
    """Verify `Authorization: Bearer <token>` bằng PUBLIC key (ES256)."""
    if not bearer:
        raise HTTPException(status_code=401, detail="missing Authorization header")
    if not bearer.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="invalid Authorization header")
    token = bearer.split(None, 1)[1]
    pub = get_public_key_pem()
    if not pub:
        raise HTTPException(status_code=500, detail="AUTH_JWT_PUBLIC_KEY not configured on server")
    try:
        return jwt.decode(token, pub, algorithms=[JWT_ALG], issuer=JWT_ISS, audience=JWT_AUD)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}")


def sign_jwt(subject: str, role: str = "service", lifetime_s: int = 3600,
             claims: Optional[Dict] = None) -> str:
    """Ký JWT ES256 bằng PRIVATE key. Chỉ node giữ private key (IAM) gọi được."""
    priv = _private_key()
    if not priv:
        raise RuntimeError(
            "JWT private key không khả dụng — chỉ node Authentication (IAM) được phép ký. "
            "Sinh khóa: python3 crypto/data/generate_jwt_keys.py"
        )
    now = int(time.time())
    payload = {
        "iss": JWT_ISS,
        "aud": JWT_AUD,
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + int(lifetime_s),
    }
    # Thuộc tính ABAC bổ sung (vd: dept="Tim_mach")
    if claims:
        payload.update(claims)
    return jwt.encode(payload, priv, algorithm=JWT_ALG)


# Tương thích ngược: tên cũ dùng khắp repo/tests.
generate_test_jwt = sign_jwt
