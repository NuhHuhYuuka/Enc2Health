"""IAM / Authentication Server (IdP) — node cấp JWT của Enc²Health.

Đây là node RIÊNG (tầng IAM), KHÔNG nằm trong Query Router/Gateway. Chỉ node này
giữ PRIVATE key ES256 để KÝ token; Router/Pool chỉ giữ PUBLIC key để verify.

Endpoints:
  POST /token        → cấp JWT ES256 cho 1 chủ thể (role + dept tùy chọn).
  GET  /public-key   → công bố PUBLIC key (PEM) để các node verify (kiểu JWKS đơn giản).
  GET  /health       → trạng thái.

Chạy:
  python3 crypto/data/generate_jwt_keys.py            # sinh keypair (1 lần)
  uvicorn iam.main:app --host 0.0.0.0 --port 8080

Ghi chú demo: chưa nối user store/mật khẩu thật — xác thực danh tính ở đây là
mô phỏng (cấp token theo role yêu cầu). Production: thêm xác thực mật khẩu/OIDC,
private key chỉ tồn tại trên node này (HSM/Vault).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from common.auth import sign_jwt, get_public_key_pem, JWT_ALG, JWT_ISS, JWT_AUD

app = FastAPI(title="Enc2Health IAM / Auth Server", version="1.0.0")

# Vai trò hợp lệ mà IAM được phép cấp (đồng bộ với ABAC).
VALID_ROLES = {"admin", "doctor", "admin_staff", "researcher", "service"}

# Mặc định mở để demo cấp token tự do. Đặt IAM_REQUIRE_LOGIN=1 để siết.
REQUIRE_LOGIN = os.environ.get("IAM_REQUIRE_LOGIN", "0") == "1"


class TokenRequest(BaseModel):
    role: str = "doctor"
    dept: Optional[str] = None
    sub: Optional[str] = None
    lifetime_s: int = 3600
    # Chỗ móc cho xác thực thật (chưa kiểm ở bản demo):
    username: Optional[str] = None
    password: Optional[str] = None


@app.post("/token")
async def issue_token(req: TokenRequest):
    """Cấp JWT ES256. IAM là node DUY NHẤT ký được (giữ private key)."""
    if req.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"role không hợp lệ: {req.role}")
    if REQUIRE_LOGIN and not (req.username and req.password):
        raise HTTPException(status_code=401, detail="cần username/password (IAM_REQUIRE_LOGIN=1)")

    claims = {"dept": req.dept} if req.dept else None
    subject = req.sub or f"{req.role}-{req.dept or 'any'}"
    try:
        token = sign_jwt(subject, req.role, lifetime_s=req.lifetime_s, claims=claims)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "access_token": token,
        "token_type": "Bearer",
        "alg": JWT_ALG,
        "role": req.role,
        "dept": req.dept,
        "expires_in": req.lifetime_s,
    }


@app.get("/public-key", response_class=PlainTextResponse)
async def public_key():
    """PUBLIC key (PEM) để Router/Pool verify token — bất đối xứng, công bố tự do."""
    pem = get_public_key_pem()
    if not pem:
        raise HTTPException(status_code=500, detail="public key chưa sẵn sàng")
    return pem


@app.get("/health")
async def health():
    return {"status": "ok", "alg": JWT_ALG, "iss": JWT_ISS, "aud": JWT_AUD,
            "has_public_key": get_public_key_pem() is not None}
