"""
KMS API Wrapper – endpoint cho Nam's Query Router.
Chạy: uvicorn kms_api.main:app --port 8001 --reload
"""
import time
import logging
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from prometheus_client import Histogram, Counter, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vault.vault_client import (
    get_dek, get_department_public_key, list_departments
)

app = FastAPI(title="Enc2Health KMS API", version="1.0.0")
log = logging.getLogger("kms_api")

# ── Prometheus metrics ──────────────────────────────────────────────────────
REQUEST_LATENCY = Histogram(
    "kms_request_latency_seconds",
    "KMS API request latency",
    ["endpoint", "status"]
)
REQUEST_COUNT = Counter(
    "kms_request_total",
    "Total KMS requests",
    ["endpoint", "dept"]
)

# ── Models ──────────────────────────────────────────────────────────────────
class DEKResponse(BaseModel):
    key_name: str
    key_b64: str
    algorithm: str
    latency_ms: float

class PublicKeyResponse(BaseModel):
    dept: str
    public_key_pem: str
    algorithm: str
    latency_ms: float


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "enc2health-kms"}


@app.get("/dek/{key_name}", response_model=DEKResponse)
def get_data_encryption_key(
    key_name: str,
    x_caller: str = Header(default="unknown")
):
    """
    Lấy DEK cho Router.
    key_name: 'gcm_dek' | 'dte_ma_benh' | 'dte_khoa' | 'ore_key'
    """
    valid_keys = {"gcm_dek", "dte_ma_benh", "dte_khoa", "ore_key"}
    if key_name not in valid_keys:
        raise HTTPException(status_code=404, detail=f"Unknown key: {key_name}")

    t0 = time.perf_counter()
    try:
        import base64
        raw_key = get_dek(key_name)
        latency = (time.perf_counter() - t0) * 1000  # ms

        REQUEST_LATENCY.labels(endpoint="get_dek", status="ok").observe(latency / 1000)
        REQUEST_COUNT.labels(endpoint="get_dek", dept="N/A").inc()

        algo_map = {
            "gcm_dek": "AES-GCM-256",
            "dte_ma_benh": "AES-SIV-256",
            "dte_khoa": "AES-SIV-256",
            "ore_key": "OPE-Boldyreva",
        }

        return DEKResponse(
            key_name=key_name,
            key_b64=base64.b64encode(raw_key).decode(),
            algorithm=algo_map[key_name],
            latency_ms=round(latency, 3)
        )
    except Exception as e:
        REQUEST_LATENCY.labels(endpoint="get_dek", status="error").observe(
            (time.perf_counter() - t0) / 1000
        )
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/pubkey/{dept}", response_model=PublicKeyResponse)
def get_public_key(dept: str, x_caller: str = Header(default="unknown")):
    """
    Lấy Public Key của Khoa cho Router để verify encryption.
    Đây là public key – không nhạy cảm, có thể cache ở client.
    """
    t0 = time.perf_counter()
    try:
        pub_pem = get_department_public_key(dept)
        latency = (time.perf_counter() - t0) * 1000

        REQUEST_LATENCY.labels(endpoint="get_pubkey", status="ok").observe(latency / 1000)
        REQUEST_COUNT.labels(endpoint="get_pubkey", dept=dept).inc()

        return PublicKeyResponse(
            dept=dept,
            public_key_pem=pub_pem.decode(),
            algorithm="ECC-P384",
            latency_ms=round(latency, 3)
        )
    except Exception as e:
        REQUEST_LATENCY.labels(endpoint="get_pubkey", status="error").observe(
            (time.perf_counter() - t0) / 1000
        )
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/departments")
def list_all_departments():
    """Danh sách Khoa có keypair trong Vault."""
    return {"departments": list_departments()}


@app.get("/metrics")
def metrics():
    """Prometheus metrics endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
