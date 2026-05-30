# Main Router Service - tích hợp T1, T2, T4, T9
from fastapi import FastAPI, HTTPException, Request
import os
from common.auth import validate_jwt_bearer
from pydantic import BaseModel
from typing import Dict
import time

from router.query_router import QueryRouter, ExecutionMode
from router.cost_model import compute_cost
from router.rbac import RBACMiddleware
from router.ecall_client import EcallClient
from router.adaptive import AdaptiveController
from router.software_executor import SoftwareExecutor

app = FastAPI(title="Enc2Health Query Router", version="1.0.0")

router   = QueryRouter()
rbac     = RBACMiddleware()
ecall    = EcallClient()
adaptive = AdaptiveController()
adaptive.start()
software_executor = SoftwareExecutor()

class QueryRequest(BaseModel):
    query_type: str
    filters: Dict = {}
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

    # T4 - RBAC check (use derived role)
    access = rbac.check(role, req.query_type)
    if not access.allowed:
        raise HTTPException(status_code=403, detail=access.reason)

    # T1 - Route decision
    decision = router.route(req.query_type, req.filters)

    # T2 - Cost estimate
    cost = compute_cost(decision.mode, n_records=1000)

    # T6 - Adaptive switching
    actual_mode = adaptive.get_execution_mode(decision.mode)

    # T9 - Execute
    if actual_mode == ExecutionMode.TEE:
        result = ecall.query(req.query_type, req.filters, role)
        if result is None:
            raise HTTPException(status_code=503, detail="ECALL pool không khả dụng")
    else:
        software_result = software_executor.query(req.query_type, req.filters)
        result = {
            "result": software_result.result,
            "n_records": software_result.n_records,
            "note": "SOFTWARE mode via MongoDB ciphertext",
        }

    # Mask nếu cần
    if isinstance(result, dict):
        result = rbac.mask_result(result, role)

    elapsed = round((time.perf_counter() - t0) * 1000, 3)
    return {
        "mode": actual_mode.value,
        "requested_mode": decision.mode.value,
        "reason": decision.reason,
        "cost_estimate": cost,
        "result": result,
        "total_latency_ms": elapsed,
        "masked_fields": access.masked_fields,
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


@app.get("/metrics")
async def metrics():
    """Return current real-time resource snapshot for adaptive routing."""
    return adaptive.get_status()
