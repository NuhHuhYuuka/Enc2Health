import os
from typing import Optional, Dict
from fastapi import HTTPException
import jwt

JWT_SECRET = os.environ.get("AUTH_JWT_SECRET")
JWT_ISS = os.environ.get("AUTH_JWT_ISS", "enc2health")
JWT_AUD = os.environ.get("AUTH_JWT_AUD", "enc2health_services")


def validate_jwt_bearer(bearer: Optional[str]) -> Dict:
    if not bearer:
        raise HTTPException(status_code=401, detail="missing Authorization header")
    if not bearer.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="invalid Authorization header")
    token = bearer.split(None, 1)[1]
    if not JWT_SECRET:
        raise HTTPException(status_code=500, detail="AUTH_JWT_SECRET not configured on server")
    try:
        claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], issuer=JWT_ISS, audience=JWT_AUD)
        return claims
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}")


def generate_test_jwt(subject: str, role: str = "service", lifetime_s: int = 3600,
                      claims: Optional[Dict] = None) -> str:
    import time
    if not JWT_SECRET:
        raise RuntimeError("AUTH_JWT_SECRET not set")
    now = int(time.time())
    payload = {
        "iss": JWT_ISS,
        "aud": JWT_AUD,
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + int(lifetime_s)
    }
    # Thuộc tính ABAC bổ sung (vd: dept="Tim_mach")
    if claims:
        payload.update(claims)
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")
