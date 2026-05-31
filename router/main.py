# Main Router Service - tích hợp T1, T2, T4, T9
from fastapi import FastAPI, HTTPException, Request
import os
from common.auth import validate_jwt_bearer
from pydantic import BaseModel, Field
from typing import Dict
import time

from router.query_router import QueryRouter, ExecutionMode
from router.cost_model import compare_costs
from router.abac import AbacPolicy, Subject
from router.ecall_client import EcallClient
from router.adaptive import AdaptiveController
from router.software_executor import SoftwareExecutor

app = FastAPI(title="Enc2Health Query Router", version="1.0.0")

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
    actual_mode = adaptive.get_execution_mode(decision.mode)

    # T9 - Execute
    ciphertext_count = None
    if actual_mode == ExecutionMode.TEE:
        ciphertexts = None
        if TEE_PUSH_CIPHERTEXT:
            ciphertexts = software_executor.fetch_vien_phi_ciphertexts(eff_filters)
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
