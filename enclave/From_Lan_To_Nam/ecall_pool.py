#!/usr/bin/env python3
"""
T8 - ECALL Task Pool (HTTP Service via FastAPI)
Copy of enclave/ecall_pool.py for handoff to Nam
"""

import base64
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import uvicorn

# Add enclave directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

POOL_WORKERS = 8  # Thread pool size for 50 doctors
PORT = 9091  # Prometheus uses 9090
HOST = "0.0.0.0"

# Global state (in-memory, process-only)
_keys: Dict[str, bytes] = {}
_keys_lock = None  # Placeholder for threading.Lock if needed
_executor = ThreadPoolExecutor(max_workers=POOL_WORKERS)

# Mock medical data (placeholder)
MOCK_PATIENT_DATA = [
    {"ma_benh": "DTE001", "tuoi": 45, "vien_phi": 1500.0},
    {"ma_benh": "DTE002", "tuoi": 32, "vien_phi": 2300.0},
    {"ma_benh": "DTE003", "tuoi": 68, "vien_phi": 5600.0},
    {"ma_benh": "DTE001", "tuoi": 52, "vien_phi": 1800.0},
    {"ma_benh": "DTE004", "tuoi": 28, "vien_phi": 900.0},
]

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Models
# ─────────────────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    """Medical query request model."""
    query_type: str  # "avg_vien_phi" | "sum_vien_phi" | "count"
    filters: Dict = {}  # {"ma_benh": "DTE-...", "tuoi_min_enc": 18, ...}
    role: str  # "doctor" | "admin" | "researcher"
    
    class Config:
        schema_extra = {
            "example": {
                "query_type": "avg_vien_phi",
                "filters": {"ma_benh": "DTE001"},
                "role": "doctor"
            }
        }


class QueryResponse(BaseModel):
    """Query response model."""
    result: float
    n_records: int
    latency_ms: float
    query_type: str


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    keys_loaded: List[str]
    workers: int
    uptime_s: float
    port: int


# ─────────────────────────────────────────────────────────────────────────────
# Core Query Logic
# ─────────────────────────────────────────────────────────────────────────────

_start_time = time.time()


def load_key(name: str, key_bytes: bytes):
    """Load a key into the pool (simulated)."""
    _keys[name] = key_bytes
    print(f"[T8 Pool] Key loaded: {name} ({len(key_bytes)} bytes)")


def decrypt_value_if_needed(value, key_name: str = "default") -> float:
    """
    Decrypt a value if it's base64-encoded ciphertext.
    In practice, would check if value is DTE-encrypted format.
    """
    if isinstance(value, str) and value.startswith("DTE-"):
        # Placeholder: actual decryption would happen here
        # For now, return mock value
        return float(hash(value) % 10000) / 10  # Mock decrypted value
    elif isinstance(value, (int, float)):
        return float(value)
    else:
        return 0.0


def _execute_medical_query(req: QueryRequest) -> Dict:
    """
    Execute a medical aggregation query.
    Runs in thread pool to allow concurrent access.
    """
    t_start = time.perf_counter()
    
    # Role-based access control (placeholder)
    allowed_roles = {"doctor", "admin", "researcher"}
    if req.role not in allowed_roles:
        raise ValueError(f"Invalid role: {req.role}")
    
    # Filter data by request
    filtered_data = MOCK_PATIENT_DATA
    
    if "ma_benh" in req.filters:
        filtered_data = [r for r in filtered_data if r.get("ma_benh") == req.filters["ma_benh"]]
    
    if "tuoi_min_enc" in req.filters:
        min_age = req.filters["tuoi_min_enc"]
        filtered_data = [r for r in filtered_data if r.get("tuoi", 0) >= min_age]
    
    n_records = len(filtered_data)
    
    # Execute aggregation
    if req.query_type == "avg_vien_phi":
        if n_records == 0:
            result = 0.0
        else:
            result = sum(r.get("vien_phi", 0) for r in filtered_data) / n_records
    
    elif req.query_type == "sum_vien_phi":
        result = sum(r.get("vien_phi", 0) for r in filtered_data)
    
    elif req.query_type == "count":
        result = float(n_records)
    
    else:
        raise ValueError(f"Unknown query type: {req.query_type}")
    
    t_end = time.perf_counter()
    latency_ms = (t_end - t_start) * 1000
    
    return {
        "result": result,
        "n_records": n_records,
        "latency_ms": latency_ms,
        "query_type": req.query_type,
    }


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Application
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Enclave ECALL Task Pool",
    description="T8 - Concurrent medical query executor",
    version="1.0.0"
)


@app.post("/query", response_model=QueryResponse, summary="Execute medical query")
async def query_endpoint(req: QueryRequest):
    """
    Execute a medical aggregation query.
    
    - **query_type**: avg_vien_phi (average), sum_vien_phi (total), count (record count)
    - **filters**: Optional filters (ma_benh, tuoi_min_enc, etc.)
    - **role**: Access control (doctor, admin, researcher)
    
    Returns:
    - **result**: Query result (float)
    - **n_records**: Number of records matched
    - **latency_ms**: Query execution time in milliseconds
    """
    try:
        # Submit to thread pool
        future = _executor.submit(_execute_medical_query, req)
        result = future.result(timeout=30)
        return QueryResponse(**result)
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TimeoutError:
        raise HTTPException(status_code=504, detail="Query timeout (>30s)")
    except Exception as e:
        print(f"[T8 Pool] Error: {e}", file=sys.stderr)
        raise HTTPException(status_code=500, detail="Internal error")


@app.get("/health", response_model=HealthResponse, summary="Health check")
async def health_endpoint():
    """
    Health check endpoint.
    
    Returns:
    - **status**: Service status (ok/degraded)
    - **keys_loaded**: List of loaded keys
    - **workers**: Thread pool size
    - **uptime_s**: Service uptime in seconds
    - **port**: Service port
    """
    uptime = time.time() - _start_time
    return HealthResponse(
        status="ok",
        keys_loaded=list(_keys.keys()),
        workers=POOL_WORKERS,
        uptime_s=uptime,
        port=PORT
    )


@app.get("/stats", summary="Pool statistics")
async def stats_endpoint():
    """Get pool statistics (for monitoring)."""
    return {
        "workers": POOL_WORKERS,
        "active_threads": _executor._work_queue.qsize() if hasattr(_executor, '_work_queue') else 'unknown',
        "mock_records": len(MOCK_PATIENT_DATA),
        "keys_loaded": len(_keys),
    }


@app.on_event("startup")
async def startup_event():
    """Startup event."""
    print("[T8 Pool] ECALL Task Pool started")
    print(f"  - Workers: {POOL_WORKERS}")
    print(f"  - Port: {PORT}")
    print(f"  - Mode: Simulation (gramine-direct)")


@app.on_event("shutdown")
async def shutdown_event():
    """Shutdown event."""
    print("[T8 Pool] ECALL Task Pool shutting down...")
    _executor.shutdown(wait=True)
    print("[T8 Pool] Shutdown complete")


# ─────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """Start the ECALL Task Pool service."""
    print("=" * 70)
    print("T8 - ECALL Task Pool (FastAPI)")
    print("=" * 70)
    print()
    print(f"Service: http://{HOST}:{PORT}")
    print(f"Workers: {POOL_WORKERS}")
    print()
    print("Endpoints:")
    print(f"  POST   /query        - Execute medical query")
    print(f"  GET    /health       - Health check")
    print(f"  GET    /stats        - Pool statistics")
    print(f"  GET    /docs         - API documentation (Swagger)")
    print()
    print("=" * 70)
    print()
    
    # Run with uvicorn
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
        access_log=True
    )


if __name__ == "__main__":
    main()
