# Main Router Service - tích hợp T1, T2, T4, T9
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict
import time

from router.query_router import QueryRouter, ExecutionMode
from router.cost_model import compute_cost
from router.rbac import RBACMiddleware
from router.ecall_client import EcallClient
from router.adaptive import AdaptiveController

app = FastAPI(title="Enc2Health Query Router", version="1.0.0")

router   = QueryRouter()
rbac     = RBACMiddleware()
ecall    = EcallClient()
adaptive = AdaptiveController()
adaptive.start()

class QueryRequest(BaseModel):
    query_type: str
    filters: Dict = {}
    role: str = "doctor"

@app.post("/query")
async def handle_query(req: QueryRequest):
    t0 = time.perf_counter()

    # T4 - RBAC check
    access = rbac.check(req.role, req.query_type)
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
        result = ecall.query(req.query_type, req.filters, req.role)
        if result is None:
            raise HTTPException(status_code=503, detail="ECALL pool không khả dụng")
    else:
        result = {"result": 0.0, "n_records": 0, "note": "SOFTWARE mode placeholder"}

    # Mask nếu cần
    if isinstance(result, dict):
        result = rbac.mask_result(result, req.role)

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
