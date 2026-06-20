"""DEPRECATED shim — auth JWT đã gộp về `common/auth.py` (ES256).

File này từng chứa bản HS256 trùng lặp. Giữ lại để import cũ không vỡ, nhưng
mọi logic ký/verify nay nằm ở common.auth (ES256/ECDSA P-256).
"""
from common.auth import (  # noqa: F401
    JWT_ALG,
    JWT_ISS,
    JWT_AUD,
    validate_jwt_bearer,
    sign_jwt,
    generate_test_jwt,
    get_public_key_pem,
)
