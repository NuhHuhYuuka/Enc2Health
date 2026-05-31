#!/usr/bin/env python3
"""
T8 - ECALL Task Pool (HTTP Service via FastAPI)
Gramine-compatible concurrent query executor for medical data.
Port: 9091 (NOT 9090 – Prometheus uses 9090)

Replaces Intel SGX SDK EDL (incompatible with Gramine) with HTTP/FastAPI Python.
"""

import base64
import os
import sys
import time
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, HTTPException, Request
# Import shared auth helpers from project `common/auth.py` without requiring a package import
import importlib.util
common_auth_path = Path(__file__).resolve().parents[2] / "common" / "auth.py"
if common_auth_path.exists():
    spec = importlib.util.spec_from_file_location("common.auth", str(common_auth_path))
    _common = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_common)
    validate_jwt_bearer = getattr(_common, "validate_jwt_bearer")
else:
    def validate_jwt_bearer(x):
        raise RuntimeError("common/auth.py not found; JWT validation unavailable")
from pydantic import BaseModel, ConfigDict, Field
import uvicorn
import ssl
import base64 as _b64

# Add enclave directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent))
# Add project root to path so `common` module is importable when running this script
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

POOL_WORKERS = 8  # Thread pool size for 50 doctors
PORT = int(os.environ.get("T8_POOL_PORT", "9091"))  # Prometheus uses 9090
HOST = os.environ.get("T8_POOL_HOST", "127.0.0.1")
POOL_RECORDS = int(os.environ.get("T8_POOL_RECORDS", "10000"))
POOL_PAYLOAD_BYTES = int(os.environ.get("T8_POOL_PAYLOAD_BYTES", "256"))
USE_INDEXES = os.environ.get("T8_POOL_USE_INDEXES", "1") != "0"

# Global state (in-memory, process-only)
_keys: Dict[str, bytes] = {}
_keys_lock = None  # Placeholder for threading.Lock if needed
_executor = ThreadPoolExecutor(max_workers=POOL_WORKERS)

def _build_mock_patient_data(n_records: int, payload_bytes: int) -> List[dict]:
    """Build a configurable in-memory EHR table for simulation benchmarks."""
    diseases = ["DTE001", "DTE002", "DTE003", "DTE004", "DTE005", "DTE006"]
    departments = ["Noi", "Ngoai", "Cap_cuu", "Tim_mach", "Than_kinh", "Nhi"]
    payload = "x" * max(0, payload_bytes)
    records: List[dict] = []

    for index in range(n_records):
        age = 18 + (index % 78)
        records.append(
            {
                "record_id": index,
                "patient_id": f"pid-{index}",
                "ma_benh": diseases[index % len(diseases)],
                "tuoi": age,
                "vien_phi": float(900 + (index % 9100)),
                "khoa": departments[index % len(departments)],
                "payload": payload,
            }
        )

    return records


MOCK_PATIENT_DATA = _build_mock_patient_data(POOL_RECORDS, POOL_PAYLOAD_BYTES)
INDEX_BY_MA_BENH = defaultdict(list)
INDEX_BY_TUOI = defaultdict(list)
INDEX_BY_KHOA = defaultdict(list)

for record in MOCK_PATIENT_DATA:
    INDEX_BY_MA_BENH[record["ma_benh"]].append(record)
    INDEX_BY_TUOI[record["tuoi"]].append(record)
    INDEX_BY_KHOA[record["khoa"]].append(record)

MAX_AGE = max(INDEX_BY_TUOI.keys(), default=0)


def _records_for_min_age(min_age: int) -> List[dict]:
    if not USE_INDEXES:
        return [record for record in MOCK_PATIENT_DATA if record.get("tuoi", 0) >= min_age]

    candidates: List[dict] = []
    for age in range(max(0, min_age), MAX_AGE + 1):
        candidates.extend(INDEX_BY_TUOI.get(age, []))
    return candidates

# ─────────────────────────────────────────────────────────────────────────────
# Pydantic Models
# ─────────────────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    """Medical query request model."""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query_type": "avg_vien_phi",
                "filters": {"ma_benh": "DTE001"},
                "role": "doctor"
            }
        }
    )

    query_type: str  # "avg_vien_phi" | "sum_vien_phi" | "count"
    filters: Dict = Field(default_factory=dict)  # {"ma_benh": "DTE-...", "tuoi_min_enc": 18, ...}
    role: str  # "doctor" | "admin" | "researcher"


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
        if USE_INDEXES:
            filtered_data = list(INDEX_BY_MA_BENH.get(req.filters["ma_benh"], []))
        else:
            filtered_data = [r for r in filtered_data if r.get("ma_benh") == req.filters["ma_benh"]]
    
    if "tuoi_min_enc" in req.filters:
        min_age = int(req.filters["tuoi_min_enc"])
        if filtered_data is MOCK_PATIENT_DATA:
            filtered_data = _records_for_min_age(min_age)
        else:
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage ECALL pool startup and shutdown."""
    print("[T8 Pool] ECALL Task Pool started")
    print(f"  - Workers: {POOL_WORKERS}")
    print(f"  - Port: {PORT}")
    print(f"  - Records: {len(MOCK_PATIENT_DATA):,} | payload_bytes={POOL_PAYLOAD_BYTES} | indexed={USE_INDEXES}")
    print("  - Mode: Simulation (gramine-direct)")
    try:
        yield
    finally:
        print("[T8 Pool] ECALL Task Pool shutting down...")
        _executor.shutdown(wait=True)
        print("[T8 Pool] Shutdown complete")


app = FastAPI(
    title="Enclave ECALL Task Pool",
    description="T8 - Concurrent medical query executor",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post("/query", response_model=QueryResponse, summary="Execute medical query")
async def query_endpoint(req: QueryRequest, request: Request):
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
    # Require internal auth if configured
    # Require Authorization: Bearer <JWT> (mTLS should also be enforced at TLS layer)
    auth_hdr = request.headers.get("authorization")
    if not auth_hdr:
        raise HTTPException(status_code=401, detail="missing Authorization header")
    validate_jwt_bearer(auth_hdr)

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
        "payload_bytes": POOL_PAYLOAD_BYTES,
        "indexed": USE_INDEXES,
        "keys_loaded": len(_keys),
    }


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
    @app.get("/attest", summary="Return RA-TLS quote stub for attestation")
    async def attest_endpoint():
        """Return a lightweight attestation stub. In production, return SGX Quote/Quote signature."""
        # In production integrate with the SGX quoting enclave to produce a real quote.
        quote = _b64.b64encode(b"mock-sgx-quote").decode()
        return {
            "mrenclave": os.environ.get("T8_EXPECTED_MRENCLAVE", "mock-mrenclave"),
            "quote": quote,
            "timestamp": int(time.time())
        }
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
