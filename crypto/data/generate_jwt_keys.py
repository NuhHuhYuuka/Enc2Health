#!/usr/bin/env python3
"""Sinh cặp khóa JWT ES256 (ECDSA P-256) cho node Authentication (IAM).

- jwt_es256_private.pem (PKCS8) — CHỈ IAM node giữ để KÝ token.
- jwt_es256_public.pem  (SPKI)  — Router/Pool giữ để VERIFY token.

Load-or-create: đã có thì giữ nguyên (khỏi vô hiệu token cũ). Ghi vào
`crypto/data/keys/` — cùng thư mục khóa dùng chung của nhóm.

Chạy:  python3 crypto/data/generate_jwt_keys.py
"""
from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

KEY_DIR = Path(__file__).resolve().parent / "keys"
PRIV = KEY_DIR / "jwt_es256_private.pem"
PUB = KEY_DIR / "jwt_es256_public.pem"


def main():
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    if PRIV.exists() and PUB.exists():
        print(f"JWT ES256 keypair đã có (giữ nguyên): {PRIV.name}, {PUB.name}")
        return

    priv = ec.generate_private_key(ec.SECP256R1())
    PRIV.write_text(priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode())
    PUB.write_text(priv.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode())
    print(f"Đã sinh JWT ES256 keypair (P-256): {PRIV}, {PUB}")
    print("→ private chỉ để trên IAM node; public copy sang Router/Pool để verify.")


if __name__ == "__main__":
    main()
