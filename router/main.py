# Main Router Service - tích hợp T1, T2, T4, T9
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import os
from pathlib import Path
from common.auth import validate_jwt_bearer
from pydantic import BaseModel, Field
from typing import Dict, Optional
import time

from router.query_router import QueryRouter, ExecutionMode
from router.cost_model import compare_costs
from router.abac import AbacPolicy, Subject
from router.ecall_client import EcallClient
from router.adaptive import AdaptiveController
from router.software_executor import SoftwareExecutor

app = FastAPI(title="Enc2Health Query Router", version="1.0.0")

# CORS: cho phép frontend ở domain khác (vd UI host trên Vercel) gọi API.
# Đặt ENC2HEALTH_CORS_ORIGINS="https://your-app.vercel.app" (phẩy để nhiều origin).
_cors = os.environ.get("ENC2HEALTH_CORS_ORIGINS", "")
if _cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors.split(",") if o.strip()],
        allow_methods=["*"], allow_headers=["*"],
    )

# UI demo + endpoint cấp token: CHỈ bật khi ENC2HEALTH_DEV_UI=1 (không dùng production).
DEV_UI = os.environ.get("ENC2HEALTH_DEV_UI", "0") == "1"
UI_HTML = Path(__file__).resolve().parent / "ui.html"

router   = QueryRouter()
abac     = AbacPolicy()
ecall    = EcallClient()
adaptive = AdaptiveController()
adaptive.start()
software_executor = SoftwareExecutor()

# Khi bật, ở TEE mode Router gom bản mã `vien_phi_enc` từ MongoDB và đẩy vào
# Enclave để giải mã + tính toán (Cloud chỉ thấy ciphertext). Mặc định tắt để
# tránh fetch/băng thông thừa khi pool chưa hỗ trợ nhận ciphertext.
TEE_PUSH_CIPHERTEXT = os.environ.get("ROUTER_TEE_PUSH_CIPHERTEXT", "0") == "1"

class QueryRequest(BaseModel):
    query_type: str
    filters: Dict = Field(default_factory=dict)
    patient_id: Optional[str] = None
    role: str = "doctor"


class SearchRequest(BaseModel):
    keyword: str
    filters: Dict = Field(default_factory=dict)
    limit: int = 20
    role: str = "doctor"

@app.post("/query")
async def handle_query(req: QueryRequest, request: Request):
    t0 = time.perf_counter()
    # Require Authorization: Bearer <JWT>
    auth_hdr = request.headers.get("authorization")
    if not auth_hdr:
        raise HTTPException(status_code=401, detail="missing Authorization header")
    claims = validate_jwt_bearer(auth_hdr)
    role = claims.get("role", "doctor")
    subject = Subject(role=role, dept=claims.get("dept"))

    # T4 - ABAC check (role + thuộc tính khoa)
    access = abac.evaluate(subject, req.query_type)
    if not access.allowed:
        raise HTTPException(status_code=403, detail=access.reason)

    # ABAC dept-scoping: tiêm filter bắt buộc, client KHÔNG thể nới rộng (scope thắng).
    eff_filters = {**req.filters, **access.scope_filters}

    # T1 - Route decision
    decision = router.route(req.query_type, eff_filters)

    # T6 - Adaptive switching
    normalized_query_type = req.query_type.lower().strip().replace("-", "_")
    is_pii_lookup = normalized_query_type in {"get_patient", "lookup_patient"}
    actual_mode = decision.mode if is_pii_lookup else adaptive.get_execution_mode(decision.mode)

    # T9 - Execute
    ciphertext_count = None
    if is_pii_lookup:
        patient_id = req.patient_id or eff_filters.get("patient_id")
        cmnd = eff_filters.get("cmnd")
        if not patient_id and not cmnd:
            raise HTTPException(status_code=400, detail="patient_id or cmnd is required for get_patient")
        try:
            if patient_id:
                pii_ciphertext = software_executor.fetch_patient_pii_ciphertext(str(patient_id))
            else:
                pii_ciphertext = software_executor.fetch_patient_pii_ciphertext_by_cmnd(str(cmnd))
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        scoped_dept = access.scope_filters.get("khoa_phong")
        if scoped_dept and pii_ciphertext["dept"] != scoped_dept:
            raise HTTPException(status_code=403, detail="ABAC dept-scope denied for this patient")

        result = ecall.query_pii(pii_ciphertext["pii_enc"], pii_ciphertext["dept"])
        if result is None:
            raise HTTPException(status_code=503, detail="ECALL pool không khả dụng")
        pii_plaintext = result.get("pii", result)

        if role in {"admin", "doctor"}:
            result_ket_qua_xn = pii_ciphertext.get("ket_qua_xn")
            result_clinical_note = pii_ciphertext.get("clinical_note")
        else:
            result_ket_qua_xn = "[MASKED]"
            result_clinical_note = "[MASKED]"

        result = {
            "patient_id": pii_ciphertext["patient_id"],
            "dept": pii_ciphertext["dept"],
            "pii": abac.mask_pii(pii_plaintext, role),
            "ma_benh": pii_ciphertext.get("ma_benh"),
            "chan_doan": pii_ciphertext.get("chan_doan"),
            "ket_qua_xn": result_ket_qua_xn,
            "clinical_note": result_clinical_note,
            "query_type": normalized_query_type,
            "n_records": 1,
        }
    elif actual_mode == ExecutionMode.TEE:
        ciphertexts = None
        if TEE_PUSH_CIPHERTEXT:
            ciphertexts = software_executor.fetch_ciphertexts(req.query_type, eff_filters)
            ciphertext_count = len(ciphertexts)
        result = ecall.query(req.query_type, eff_filters, role, ciphertexts=ciphertexts)
        if result is None:
            raise HTTPException(status_code=503, detail="ECALL pool không khả dụng")
    else:
        software_result = software_executor.query(req.query_type, eff_filters)
        result = {
            "result": software_result.result,
            "n_records": software_result.n_records,
            "note": "SOFTWARE mode via MongoDB ciphertext",
        }

    # T2 - Cost estimate dựa trên số bản ghi THẬT (so sánh C_soft vs C_TEE)
    n_records = int(result.get("n_records", 0) or 0) if isinstance(result, dict) else 0
    cost = compare_costs(n_records)

    # Mask nếu cần
    if isinstance(result, dict):
        result = abac.mask_result(result, role)

    elapsed = round((time.perf_counter() - t0) * 1000, 3)
    response = {
        "mode": actual_mode.value,
        "requested_mode": decision.mode.value,
        "reason": decision.reason,
        "cost_estimate": cost,
        "result": result,
        "total_latency_ms": elapsed,
        "masked_fields": access.masked_fields,
        "abac_scope": access.scope_filters,   # khoa bị giới hạn (nếu có)
    }
    if ciphertext_count is not None:
        response["ciphertext_pushed"] = ciphertext_count
    return response


@app.post("/search")
async def handle_search(req: SearchRequest, request: Request):
    """Static SSE keyword search over encrypted clinical index."""
    t0 = time.perf_counter()
    auth_hdr = request.headers.get("authorization")
    if not auth_hdr:
        raise HTTPException(status_code=401, detail="missing Authorization header")
    claims = validate_jwt_bearer(auth_hdr)
    role = claims.get("role", req.role)
    subject = Subject(role=role, dept=claims.get("dept"))

    access = abac.evaluate(subject, "keyword_search")
    if not access.allowed:
        raise HTTPException(status_code=403, detail=access.reason)
    eff_filters = {**req.filters, **access.scope_filters}
    decision = router.route("keyword_search", eff_filters)

    try:
        search_result = software_executor.keyword_search(req.keyword, eff_filters, limit=req.limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    visible_postings = role in {"admin", "doctor", "admin_staff"}
    response_result = {
        "keyword": search_result["keyword"],
        "count": search_result.get("filtered_records", search_result.get("n_records", 0)),
        "global_volume_leakage": search_result.get("volume_leakage", 0),
        "token": search_result.get("token"),
        "note": search_result.get("note"),
    }
    if visible_postings:
        response_result["postings"] = search_result.get("postings", [])
    else:
        response_result["postings"] = "[MASKED]"

    return {
        "mode": decision.mode.value,
        "requested_mode": decision.mode.value,
        "reason": decision.reason,
        "result": response_result,
        "total_latency_ms": round((time.perf_counter() - t0) * 1000, 3),
        "masked_fields": access.masked_fields + ([] if visible_postings else ["postings"]),
        "abac_scope": access.scope_filters,
        "leakage": {
            "search_pattern": "same keyword yields same HMAC token",
            "volume": "n_records is visible in the SSE index",
            "access_pattern": "posting ids are decrypted by Router and may be role-masked",
        },
    }

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "ecall_pool": ecall.health_check()
    }

@app.get("/adaptive")
async def adaptive_status():
    return adaptive.get_status()


@app.post("/adaptive/simulate")
async def adaptive_simulate(pressure: float | None = None):
    """Núm mô phỏng áp lực EPC để demo/kiểm thử adaptive fallback.

    - `pressure` 0.0–1.0: ép áp lực EPC (>= 0.80 → fallback).
    - bỏ trống / null: trở lại tín hiệu THẬT từ prober.
    Đây là công cụ DEMO/TEST, không dùng cho production routing.
    """
    adaptive.set_simulated_pressure(pressure)
    return adaptive.get_status()


@app.get("/metrics")
async def metrics():
    """Return current real-time resource snapshot for adaptive routing."""
    return adaptive.get_status()


@app.get("/nodes")
async def nodes_status():
    """Trạng thái LIVE của từng node (cho demo phân tán — Router probe qua mạng).

    Router (máy Nam) tự kiểm: Pool (máy Lan qua ECALL_POOL_URL), MongoDB (máy Long
    qua MONGO_URI), Vault (máy Long qua VAULT_ADDR) → cho thấy các node ở các MÁY
    khác nhau có liên lạc được không.
    """
    pool_ok = ecall.health_check()
    try:
        mongo_ok = software_executor._detect_mongo_available()
    except Exception:
        mongo_ok = False
    vault_ok = False
    try:
        import httpx
        vault_addr = os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200").rstrip("/")
        httpx.get(vault_addr + "/v1/sys/health", timeout=1.5)
        vault_ok = True  # bất kỳ phản hồi nào = Vault reachable (kể cả sealed)
    except Exception:
        vault_ok = False
    return {
        "nodes": [
            {"id": "client",  "label": "Client (EMR / Bác sĩ)",        "owner": "—",    "status": "ok"},
            {"id": "router",  "label": "Query Router (Middleware)",    "owner": "Nam",  "port": 8000,  "status": "ok"},
            {"id": "mongo",   "label": "MongoDB (Cloud · ciphertext)", "owner": "Long", "port": 27017, "status": "ok" if mongo_ok else "down"},
            {"id": "enclave", "label": "ECALL Pool / Enclave (TEE)",   "owner": "Lan",  "port": 9091,  "status": "ok" if pool_ok else "down"},
            {"id": "vault",   "label": "HashiCorp Vault (KMS)",        "owner": "Long", "port": 8200,  "status": "ok" if vault_ok else "down"},
        ]
    }


# ── UI demo (chỉ bật khi ENC2HEALTH_DEV_UI=1) ───────────────────────────────
@app.get("/ui", response_class=HTMLResponse)
async def ui_page():
    """Trang demo web. Bật bằng env ENC2HEALTH_DEV_UI=1."""
    if not DEV_UI:
        raise HTTPException(status_code=404, detail="UI tắt (đặt ENC2HEALTH_DEV_UI=1)")
    return HTMLResponse(UI_HTML.read_text(encoding="utf-8"))


class TokenRequest(BaseModel):
    role: str = "doctor"
    dept: Optional[str] = None


@app.post("/dev/token")
async def dev_token(req: TokenRequest):
    """Cấp JWT cho UI demo theo vai trò/khoa. CHỈ dev (ENC2HEALTH_DEV_UI=1).

    KHÔNG dùng ở production — đây là tiện ích demo để khỏi phải tự sinh token.
    """
    if not DEV_UI:
        raise HTTPException(status_code=404, detail="disabled")
    from common.auth import generate_test_jwt
    claims = {"dept": req.dept} if req.dept else None
    token = generate_test_jwt(f"ui-{req.role}", req.role, claims=claims)
    return {"token": token, "role": req.role, "dept": req.dept}
